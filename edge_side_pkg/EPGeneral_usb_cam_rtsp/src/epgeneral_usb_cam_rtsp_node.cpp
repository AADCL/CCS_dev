#include <arpa/inet.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <cv_bridge/cv_bridge.h>
#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include <gst/rtsp-server/rtsp-server.h>
#include <image_transport/image_transport.h>
#include <ros/ros.h>
#include <sensor_msgs/Image.h>
#include <sensor_msgs/CompressedImage.h>

class UsbCamRtspNode {
 public:
  UsbCamRtspNode()
      : nh_(), pnh_("~"), image_transport_(nh_), main_loop_(nullptr), server_(nullptr),
        server_source_id_(0), frame_sequence_(0), received_frame_(false), shutting_down_(false) {
    loadConfiguration();
    gst_init(nullptr, nullptr);
    startRtspServer();
    if (image_message_type_ == "sensor_msgs/Image") {
      image_subscriber_ = image_transport_.subscribe(
          image_topic_, 1, &UsbCamRtspNode::imageCallback, this,
          image_transport::TransportHints("raw"));
    } else {
      compressed_subscriber_ = nh_.subscribe(
          image_topic_, 1, &UsbCamRtspNode::compressedImageCallback, this);
    }
    frame_watchdog_ = nh_.createWallTimer(
        ros::WallDuration(1.0), &UsbCamRtspNode::watchdogCallback, this);
    ROS_INFO_STREAM("epgeneral_usb_cam_rtsp ready device_id=" << device_id_ << " device_ip="
                    << device_ip_ << " url=rtsp://" << device_ip_ << ":" << rtsp_port_
                    << rtsp_mount_point_ << " image_topic=" << image_topic_
                    << " image_message_type=" << image_message_type_
                    << " output=" << output_width_ << "x" << output_height_);
  }

  ~UsbCamRtspNode() {
    shutting_down_ = true;
    if (main_loop_ != nullptr) {
      g_main_loop_quit(main_loop_);
    }
    if (glib_thread_.joinable()) {
      glib_thread_.join();
    }
    {
      std::lock_guard<std::mutex> lock(source_mutex_);
      for (GstElement* source : app_sources_) {
        g_signal_handlers_disconnect_by_data(source, this);
        gst_app_src_end_of_stream(GST_APP_SRC(source));
        g_object_unref(source);
      }
      app_sources_.clear();
    }
    {
      std::lock_guard<std::mutex> lock(callback_mutex_);
    }
    if (server_source_id_ != 0) {
      g_source_remove(server_source_id_);
    }
    if (server_ != nullptr) {
      g_object_unref(server_);
    }
    if (main_loop_ != nullptr) {
      g_main_loop_unref(main_loop_);
    }
    ROS_INFO("epgeneral_usb_cam_rtsp stopped");
  }

 private:
  void loadConfiguration() {
    int schema_version = 0;
    if (!nh_.getParam("/edge_device/schema_version", schema_version) || schema_version != 1) {
      throw std::runtime_error("/edge_device/schema_version must be 1");
    }
    if (!nh_.getParam("/edge_device/device/id", device_id_) || device_id_.empty()) {
      throw std::runtime_error("/edge_device/device/id is required");
    }
    if (!nh_.getParam("/edge_device/device/ip", device_ip_) || !validIpAddress(device_ip_)) {
      throw std::runtime_error("/edge_device/device/ip must be a valid IPv4 or IPv6 address");
    }
    pnh_.param<std::string>("image_topic", image_topic_, "/camera/image_raw");
    pnh_.param<std::string>("image_message_type", image_message_type_, "sensor_msgs/Image");
    pnh_.param<std::string>("rtsp_bind_address", bind_address_, "0.0.0.0");
    pnh_.param<std::string>("rtsp_mount_point", rtsp_mount_point_, "/usb_cam");
    pnh_.param("rtsp_port", rtsp_port_, 8554);
    if (!pnh_.getParam("output_width", output_width_)) {
      pnh_.param("image_width", output_width_, 640);
    }
    if (!pnh_.getParam("output_height", output_height_)) {
      pnh_.param("image_height", output_height_, 480);
    }
    pnh_.param("framerate", framerate_, 30);
    pnh_.param("bitrate_kbps", bitrate_kbps_, 2000);
    pnh_.param("frame_timeout_seconds", frame_timeout_seconds_, 5.0);
    if (image_topic_.empty() || image_topic_[0] != '/') {
      throw std::runtime_error("image_topic must be an absolute ROS topic");
    }
    if (image_message_type_ != "sensor_msgs/Image" &&
        image_message_type_ != "sensor_msgs/CompressedImage") {
      throw std::runtime_error(
          "image_message_type must be sensor_msgs/Image or sensor_msgs/CompressedImage");
    }
    if (rtsp_port_ < 1 || rtsp_port_ > 65535 || output_width_ < 1 ||
        output_height_ < 1 || framerate_ < 1 || framerate_ > 120 ||
        bitrate_kbps_ < 1 || frame_timeout_seconds_ <= 0.0) {
      throw std::runtime_error("video or RTSP numeric configuration is out of range");
    }
    if (rtsp_mount_point_.empty() || rtsp_mount_point_[0] != '/') {
      throw std::runtime_error("rtsp_mount_point must start with '/'");
    }
    if (rtsp_port_ != 8554 || rtsp_mount_point_ != "/usb_cam") {
      ROS_WARN("Ground station v0.3.0 expects RTSP port 8554 and mount point /usb_cam");
    }
  }

  static bool validIpAddress(const std::string& address) {
    struct in_addr ipv4;
    struct in6_addr ipv6;
    return inet_pton(AF_INET, address.c_str(), &ipv4) == 1 ||
           inet_pton(AF_INET6, address.c_str(), &ipv6) == 1;
  }

  void startRtspServer() {
    main_loop_ = g_main_loop_new(nullptr, FALSE);
    server_ = gst_rtsp_server_new();
    const std::string service = std::to_string(rtsp_port_);
    g_object_set(server_, "address", bind_address_.c_str(), "service", service.c_str(), nullptr);
    g_signal_connect(server_, "client-connected", G_CALLBACK(&UsbCamRtspNode::clientConnected), this);

    GstRTSPMountPoints* mounts = gst_rtsp_server_get_mount_points(server_);
    GstRTSPMediaFactory* factory = gst_rtsp_media_factory_new();
    const std::string pipeline = buildPipeline();
    gst_rtsp_media_factory_set_launch(factory, pipeline.c_str());
    gst_rtsp_media_factory_set_shared(factory, TRUE);
    g_signal_connect(factory, "media-configure", G_CALLBACK(&UsbCamRtspNode::mediaConfigure), this);
    gst_rtsp_mount_points_add_factory(mounts, rtsp_mount_point_.c_str(), factory);
    g_object_unref(mounts);

    server_source_id_ = gst_rtsp_server_attach(server_, nullptr);
    if (server_source_id_ == 0) {
      throw std::runtime_error("failed to bind GStreamer RTSP server");
    }
    glib_thread_ = std::thread([this]() { g_main_loop_run(main_loop_); });
  }

  std::string buildPipeline() const {
    std::ostringstream pipeline;
    pipeline << "( appsrc name=source is-live=true block=false format=time "
             << "caps=video/x-raw,format=BGR,width=" << output_width_
             << ",height=" << output_height_ << ",framerate=" << framerate_ << "/1 "
             << "! queue max-size-buffers=2 leaky=downstream "
             << "! videoconvert ! video/x-raw,format=I420 "
             << "! x264enc tune=zerolatency speed-preset=ultrafast bitrate=" << bitrate_kbps_
             << " key-int-max=" << framerate_
             << " ! rtph264pay name=pay0 pt=96 config-interval=1 )";
    return pipeline.str();
  }

  void imageCallback(const sensor_msgs::ImageConstPtr& message) {
    try {
      const cv_bridge::CvImageConstPtr converted = cv_bridge::toCvShare(message, "bgr8");
      storeFrame(converted->image);
    } catch (const cv_bridge::Exception& error) {
      ROS_ERROR_THROTTLE(5.0, "epgeneral_usb_cam_rtsp image conversion failed: %s", error.what());
    } catch (const std::exception& error) {
      ROS_ERROR_THROTTLE(5.0, "epgeneral_usb_cam_rtsp raw image processing failed: %s", error.what());
    }
  }

  void compressedImageCallback(const sensor_msgs::CompressedImageConstPtr& message) {
    try {
      if (message->data.empty()) {
        throw std::runtime_error("compressed image payload is empty");
      }
      const cv_bridge::CvImagePtr converted = cv_bridge::toCvCopy(message, "bgr8");
      storeFrame(converted->image);
    } catch (const cv_bridge::Exception& error) {
      ROS_ERROR_THROTTLE(5.0, "epgeneral_usb_cam_rtsp compressed image conversion failed: %s", error.what());
    } catch (const std::exception& error) {
      ROS_ERROR_THROTTLE(5.0, "epgeneral_usb_cam_rtsp compressed image processing failed: %s", error.what());
    }
  }

  void storeFrame(const cv::Mat& input) {
    if (input.data == nullptr || input.rows < 1 || input.cols < 1 || input.type() != CV_8UC3) {
      throw std::runtime_error("converted frame must be non-empty BGR8");
    }
    const std::size_t output_bytes = static_cast<std::size_t>(output_width_) *
                                     static_cast<std::size_t>(output_height_) * 3;
    std::vector<std::uint8_t> output(output_bytes);
    if (input.cols == output_width_ && input.rows == output_height_) {
      const std::size_t row_bytes = static_cast<std::size_t>(output_width_) * 3;
      for (int y = 0; y < output_height_; ++y) {
        std::memcpy(output.data() + static_cast<std::size_t>(y) * row_bytes,
                    input.ptr<std::uint8_t>(y), row_bytes);
      }
    } else {
      for (int y = 0; y < output_height_; ++y) {
        const int source_y = y * input.rows / output_height_;
        const std::uint8_t* source_row = input.ptr<std::uint8_t>(source_y);
        std::uint8_t* output_row = output.data() +
            static_cast<std::size_t>(y) * static_cast<std::size_t>(output_width_) * 3;
        for (int x = 0; x < output_width_; ++x) {
          const int source_x = x * input.cols / output_width_;
          std::memcpy(output_row + static_cast<std::size_t>(x) * 3,
                      source_row + static_cast<std::size_t>(source_x) * 3, 3);
        }
      }
    }
    {
      std::lock_guard<std::mutex> lock(frame_mutex_);
      latest_frame_.swap(output);
      last_frame_time_ = ros::WallTime::now();
      received_frame_ = true;
    }
    ROS_INFO_ONCE("epgeneral_usb_cam_rtsp received first ROS video frame");
  }

  void watchdogCallback(const ros::WallTimerEvent&) {
    std::lock_guard<std::mutex> lock(frame_mutex_);
    if (!received_frame_) {
      ROS_WARN_THROTTLE(5.0, "epgeneral_usb_cam_rtsp is waiting for camera frames on %s", image_topic_.c_str());
      return;
    }
    if ((ros::WallTime::now() - last_frame_time_).toSec() > frame_timeout_seconds_) {
      ROS_ERROR_THROTTLE(5.0, "epgeneral_usb_cam_rtsp camera frames have stopped");
    }
  }

  static void mediaConfigure(GstRTSPMediaFactory*, GstRTSPMedia* media, gpointer user_data) {
    UsbCamRtspNode* node = static_cast<UsbCamRtspNode*>(user_data);
    GstElement* element = gst_rtsp_media_get_element(media);
    GstElement* source = gst_bin_get_by_name_recurse_up(GST_BIN(element), "source");
    if (source != nullptr) {
      std::lock_guard<std::mutex> lock(node->source_mutex_);
      if (!node->shutting_down_) {
        g_signal_connect(source, "need-data", G_CALLBACK(&UsbCamRtspNode::needData), node);
        node->app_sources_.push_back(source);
      } else {
        g_object_unref(source);
      }
    } else {
      ROS_ERROR("epgeneral_usb_cam_rtsp could not locate GStreamer appsrc");
    }
    g_object_unref(element);
  }

  static void needData(GstElement* appsrc, guint, gpointer user_data) {
    UsbCamRtspNode* node = static_cast<UsbCamRtspNode*>(user_data);
    std::lock_guard<std::mutex> callback_lock(node->callback_mutex_);
    if (node->shutting_down_) {
      return;
    }
    std::vector<std::uint8_t> frame;
    {
      std::lock_guard<std::mutex> lock(node->frame_mutex_);
      frame = node->latest_frame_;
    }
    if (frame.empty()) {
      frame.assign(static_cast<std::size_t>(node->output_width_) *
                       static_cast<std::size_t>(node->output_height_) * 3,
                   0);
    }
    GstBuffer* buffer = gst_buffer_new_allocate(nullptr, frame.size(), nullptr);
    gst_buffer_fill(buffer, 0, frame.data(), frame.size());
    const std::uint64_t sequence = node->frame_sequence_.fetch_add(1);
    const GstClockTime duration = gst_util_uint64_scale_int(1, GST_SECOND, node->framerate_);
    GST_BUFFER_PTS(buffer) = sequence * duration;
    GST_BUFFER_DTS(buffer) = GST_BUFFER_PTS(buffer);
    GST_BUFFER_DURATION(buffer) = duration;
    const GstFlowReturn result = gst_app_src_push_buffer(GST_APP_SRC(appsrc), buffer);
    if (result != GST_FLOW_OK) {
      ROS_WARN_THROTTLE(5.0, "epgeneral_usb_cam_rtsp appsrc push returned %d", result);
    }
  }

  static void clientConnected(GstRTSPServer*, GstRTSPClient* client, gpointer user_data) {
    UsbCamRtspNode* node = static_cast<UsbCamRtspNode*>(user_data);
    ROS_INFO_STREAM("epgeneral_usb_cam_rtsp RTSP client connected for device " << node->device_id_);
    g_signal_connect(client, "closed", G_CALLBACK(&UsbCamRtspNode::clientClosed), user_data);
  }

  static void clientClosed(GstRTSPClient*, gpointer user_data) {
    UsbCamRtspNode* node = static_cast<UsbCamRtspNode*>(user_data);
    ROS_INFO_STREAM("epgeneral_usb_cam_rtsp RTSP client disconnected for device " << node->device_id_);
  }

  ros::NodeHandle nh_;
  ros::NodeHandle pnh_;
  image_transport::ImageTransport image_transport_;
  image_transport::Subscriber image_subscriber_;
  ros::Subscriber compressed_subscriber_;
  ros::WallTimer frame_watchdog_;
  std::string device_id_;
  std::string device_ip_;
  std::string image_topic_;
  std::string image_message_type_;
  std::string bind_address_;
  std::string rtsp_mount_point_;
  int rtsp_port_;
  int output_width_;
  int output_height_;
  int framerate_;
  int bitrate_kbps_;
  double frame_timeout_seconds_;
  GMainLoop* main_loop_;
  GstRTSPServer* server_;
  guint server_source_id_;
  std::thread glib_thread_;
  std::mutex source_mutex_;
  std::mutex callback_mutex_;
  std::vector<GstElement*> app_sources_;
  std::mutex frame_mutex_;
  std::vector<std::uint8_t> latest_frame_;
  ros::WallTime last_frame_time_;
  std::atomic<std::uint64_t> frame_sequence_;
  bool received_frame_;
  std::atomic<bool> shutting_down_;
};

int main(int argc, char** argv) {
  ros::init(argc, argv, "epgeneral_usb_cam_rtsp");
  try {
    UsbCamRtspNode node;
    ros::spin();
    return 0;
  } catch (const std::exception& error) {
    ROS_FATAL("epgeneral_usb_cam_rtsp startup failed: %s", error.what());
    return 1;
  }
}
