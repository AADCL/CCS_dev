#include <arpa/inet.h>

#include <atomic>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <cv_bridge/cv_bridge.h>
#include <gio/gio.h>
#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include <image_transport/image_transport.h>
#include <ros/ros.h>
#include <sensor_msgs/CompressedImage.h>
#include <sensor_msgs/Image.h>

class VideoSrtNode {
 public:
  VideoSrtNode()
      : nh_(), pnh_("~"), image_transport_(nh_), pipeline_(nullptr), appsrc_(nullptr), srt_sink_(nullptr),
        main_loop_(nullptr), bus_watch_id_(0), sequence_(0), received_frame_(false),
        shutting_down_(false) {
    loadConfiguration();
    gst_init(nullptr, nullptr);
    validateGstreamerPlugins();
    startPipeline();
    if (image_message_type_ == "sensor_msgs/Image") {
      image_subscriber_ = image_transport_.subscribe(
          image_topic_, 1, &VideoSrtNode::imageCallback, this,
          image_transport::TransportHints("raw"));
    } else {
      compressed_subscriber_ = nh_.subscribe(
          image_topic_, 1, &VideoSrtNode::compressedImageCallback, this);
    }
    frame_watchdog_ = nh_.createWallTimer(
        ros::WallDuration(1.0), &VideoSrtNode::watchdogCallback, this);
    ROS_INFO_STREAM("epgeneral_video_srt ready device_id=" << device_id_
                    << " listener=srt://" << device_ip_ << ":" << srt_port_
                    << " image_topic=" << image_topic_ << " image_message_type="
                    << image_message_type_ << " output=" << output_width_ << "x"
                    << output_height_ << " latency_ms=" << srt_latency_ms_);
  }

  ~VideoSrtNode() {
    shutting_down_ = true;
    if (appsrc_ != nullptr) gst_app_src_end_of_stream(GST_APP_SRC(appsrc_));
    if (pipeline_ != nullptr) gst_element_set_state(pipeline_, GST_STATE_NULL);
    if (main_loop_ != nullptr) g_main_loop_quit(main_loop_);
    if (glib_thread_.joinable()) glib_thread_.join();
    if (bus_watch_id_ != 0) g_source_remove(bus_watch_id_);
    if (appsrc_ != nullptr) gst_object_unref(appsrc_);
    if (srt_sink_ != nullptr) gst_object_unref(srt_sink_);
    if (pipeline_ != nullptr) gst_object_unref(pipeline_);
    if (main_loop_ != nullptr) g_main_loop_unref(main_loop_);
    ROS_INFO("epgeneral_video_srt stopped");
  }

 private:
  void loadConfiguration() {
    int schema_version = 0;
    if (!nh_.getParam("/edge_device/schema_version", schema_version) || schema_version != 1)
      throw std::runtime_error("/edge_device/schema_version must be 1");
    if (!nh_.getParam("/edge_device/device/id", device_id_) || device_id_.empty())
      throw std::runtime_error("/edge_device/device/id is required");
    if (!nh_.getParam("/edge_device/device/ip", device_ip_) || !validIpAddress(device_ip_))
      throw std::runtime_error("/edge_device/device/ip must be a valid IPv4 or IPv6 address");
    pnh_.param<std::string>("image_topic", image_topic_, "/camera/image_raw");
    pnh_.param<std::string>("image_message_type", image_message_type_, "sensor_msgs/Image");
    pnh_.param<std::string>("srt_bind_address", bind_address_, "0.0.0.0");
    pnh_.param("srt_port", srt_port_, 9000);
    pnh_.param("srt_latency_ms", srt_latency_ms_, 120);
    if (!pnh_.getParam("output_width", output_width_)) pnh_.param("image_width", output_width_, 640);
    if (!pnh_.getParam("output_height", output_height_)) pnh_.param("image_height", output_height_, 480);
    pnh_.param("framerate", framerate_, 30);
    pnh_.param("bitrate_kbps", bitrate_kbps_, 2000);
    pnh_.param("frame_timeout_seconds", frame_timeout_seconds_, 5.0);
    if (image_topic_.empty() || image_topic_[0] != '/')
      throw std::runtime_error("image_topic must be an absolute ROS topic");
    if (image_message_type_ != "sensor_msgs/Image" &&
        image_message_type_ != "sensor_msgs/CompressedImage")
      throw std::runtime_error("image_message_type must be sensor_msgs/Image or sensor_msgs/CompressedImage");
    if (!validIpAddress(bind_address_) || srt_port_ < 1 || srt_port_ > 65535 ||
        srt_latency_ms_ < 20 || srt_latency_ms_ > 8000 || output_width_ < 1 ||
        output_height_ < 1 || framerate_ < 1 || framerate_ > 120 ||
        bitrate_kbps_ < 1 || frame_timeout_seconds_ <= 0.0)
      throw std::runtime_error("video or SRT numeric configuration is out of range");
  }

  static bool validIpAddress(const std::string& address) {
    struct in_addr ipv4;
    struct in6_addr ipv6;
    return inet_pton(AF_INET, address.c_str(), &ipv4) == 1 ||
           inet_pton(AF_INET6, address.c_str(), &ipv6) == 1;
  }

  std::string listenerUri() const {
    std::string host = bind_address_;
    if (host == "0.0.0.0" || host == "::") host.clear();
    if (host.find(':') != std::string::npos && !host.empty()) host = "[" + host + "]";
    return "srt://" + host + ":" + std::to_string(srt_port_) +
           "?mode=listener&transtype=live&latency=" +
           std::to_string(static_cast<long long>(srt_latency_ms_) * 1000LL);
  }

  static void validateGstreamerPlugins() {
    const char* elements[] = {"appsrc", "videoconvert", "x264enc", "h264parse", "mpegtsmux", "srtsink"};
    for (const char* name : elements) {
      GstElementFactory* factory = gst_element_factory_find(name);
      if (factory == nullptr) {
        gchar* version = gst_version_string();
        const std::string message =
            std::string("required GStreamer element is unavailable: ") + name +
            ". Install gstreamer1.0-plugins-bad (srtsink), check GST_PLUGIN_PATH; runtime=" +
            (version != nullptr ? version : "unknown");
        g_free(version);
        throw std::runtime_error(message);
      }
      gst_object_unref(factory);
    }
  }

  std::string buildPipeline() const {
    std::ostringstream pipeline;
    pipeline << "appsrc name=source is-live=true block=false format=time do-timestamp=false "
             << "caps=video/x-raw,format=BGR,width=" << output_width_ << ",height="
             << output_height_ << ",framerate=" << framerate_ << "/1 "
             << "! queue max-size-buffers=2 leaky=downstream "
             << "! videoconvert ! video/x-raw,format=I420 "
             << "! x264enc tune=zerolatency speed-preset=ultrafast bitrate=" << bitrate_kbps_
             << " key-int-max=" << framerate_ << " bframes=0 byte-stream=true aud=true "
             << "! video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au "
             << "! h264parse config-interval=-1 ! mpegtsmux alignment=7 "
             << "! srtsink name=srt_output uri=\"" << listenerUri() << "\" sync=false";
    return pipeline.str();
  }

  void startPipeline() {
    GError* error = nullptr;
    const std::string description = buildPipeline();
    ROS_INFO_STREAM("epgeneral_video_srt GStreamer pipeline: " << description);
    pipeline_ = gst_parse_launch(description.c_str(), &error);
    if (pipeline_ == nullptr || error != nullptr) {
      const std::string message = error != nullptr ? error->message : "unknown parser error";
      if (error != nullptr) g_error_free(error);
      throw std::runtime_error("failed to create SRT pipeline: " + message);
    }
    appsrc_ = gst_bin_get_by_name(GST_BIN(pipeline_), "source");
    if (appsrc_ == nullptr) throw std::runtime_error("failed to locate GStreamer appsrc");
    srt_sink_ = gst_bin_get_by_name(GST_BIN(pipeline_), "srt_output");
    if (srt_sink_ == nullptr) throw std::runtime_error("failed to locate GStreamer srtsink");
    if (g_signal_lookup("caller-connecting", G_OBJECT_TYPE(srt_sink_)) != 0) {
      g_signal_connect(srt_sink_, "caller-connecting",
                       G_CALLBACK(&VideoSrtNode::callerConnecting), this);
    }
    g_signal_connect(srt_sink_, "caller-added", G_CALLBACK(&VideoSrtNode::callerAdded), this);
    g_signal_connect(srt_sink_, "caller-removed", G_CALLBACK(&VideoSrtNode::callerRemoved), this);
    GstBus* bus = gst_element_get_bus(pipeline_);
    bus_watch_id_ = gst_bus_add_watch(bus, &VideoSrtNode::busMessage, this);
    gst_object_unref(bus);
    main_loop_ = g_main_loop_new(nullptr, FALSE);
    const GstStateChangeReturn state_result = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
    if (state_result == GST_STATE_CHANGE_FAILURE)
      throw std::runtime_error("failed to start SRT Listener pipeline");
    glib_thread_ = std::thread([this]() { g_main_loop_run(main_loop_); });
    ROS_INFO_STREAM("epgeneral_video_srt SRT listener bound to " << bind_address_ << ":" << srt_port_
                    << "; waiting for a ground-station caller");
  }

  void imageCallback(const sensor_msgs::ImageConstPtr& message) {
    try { pushFrame(cv_bridge::toCvShare(message, "bgr8")->image); }
    catch (const std::exception& error) {
      ROS_ERROR_THROTTLE(5.0, "epgeneral_video_srt raw image processing failed: %s", error.what());
    }
  }

  void compressedImageCallback(const sensor_msgs::CompressedImageConstPtr& message) {
    try {
      if (message->data.empty()) throw std::runtime_error("compressed image payload is empty");
      pushFrame(cv_bridge::toCvCopy(message, "bgr8")->image);
    } catch (const std::exception& error) {
      ROS_ERROR_THROTTLE(5.0, "epgeneral_video_srt compressed image processing failed: %s", error.what());
    }
  }

  void pushFrame(const cv::Mat& input) {
    if (input.data == nullptr || input.rows < 1 || input.cols < 1 || input.type() != CV_8UC3)
      throw std::runtime_error("converted frame must be non-empty BGR8");
    std::vector<std::uint8_t> frame(static_cast<std::size_t>(output_width_) * output_height_ * 3);
    for (int y = 0; y < output_height_; ++y) {
      const std::uint8_t* source_row = input.ptr<std::uint8_t>(y * input.rows / output_height_);
      std::uint8_t* output_row = frame.data() + static_cast<std::size_t>(y) * output_width_ * 3;
      for (int x = 0; x < output_width_; ++x)
        std::memcpy(output_row + static_cast<std::size_t>(x) * 3,
                    source_row + static_cast<std::size_t>(x * input.cols / output_width_) * 3, 3);
    }
    std::lock_guard<std::mutex> lock(push_mutex_);
    if (shutting_down_) return;
    GstBuffer* buffer = gst_buffer_new_allocate(nullptr, frame.size(), nullptr);
    gst_buffer_fill(buffer, 0, frame.data(), frame.size());
    const GstClockTime duration = gst_util_uint64_scale_int(1, GST_SECOND, framerate_);
    GST_BUFFER_PTS(buffer) = sequence_ * duration;
    GST_BUFFER_DTS(buffer) = GST_BUFFER_PTS(buffer);
    GST_BUFFER_DURATION(buffer) = duration;
    ++sequence_;
    const GstFlowReturn result = gst_app_src_push_buffer(GST_APP_SRC(appsrc_), buffer);
    if (result != GST_FLOW_OK)
      ROS_WARN_THROTTLE(5.0, "epgeneral_video_srt appsrc push returned %d", result);
    last_frame_time_ = ros::WallTime::now();
    received_frame_ = true;
    ROS_INFO_ONCE("epgeneral_video_srt pushed first frame into H.264/MPEG-TS encoder");
  }

  void watchdogCallback(const ros::WallTimerEvent&) {
    if (!received_frame_)
      ROS_WARN_THROTTLE(5.0, "epgeneral_video_srt is waiting for camera frames on %s", image_topic_.c_str());
    else if ((ros::WallTime::now() - last_frame_time_).toSec() > frame_timeout_seconds_)
      ROS_ERROR_THROTTLE(5.0, "epgeneral_video_srt camera frames have stopped");
  }

  static gboolean busMessage(GstBus*, GstMessage* message, gpointer user_data) {
    VideoSrtNode* node = static_cast<VideoSrtNode*>(user_data);
    if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR || GST_MESSAGE_TYPE(message) == GST_MESSAGE_WARNING) {
      GError* error = nullptr;
      gchar* debug = nullptr;
      if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
        gst_message_parse_error(message, &error, &debug);
        ROS_ERROR("epgeneral_video_srt pipeline error: %s", error->message);
      } else {
        gst_message_parse_warning(message, &error, &debug);
        ROS_WARN("epgeneral_video_srt pipeline warning: %s", error->message);
      }
      g_clear_error(&error);
      g_free(debug);
    } else if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_STATE_CHANGED &&
               GST_MESSAGE_SRC(message) == GST_OBJECT(node->pipeline_)) {
      GstState old_state, new_state, pending;
      gst_message_parse_state_changed(message, &old_state, &new_state, &pending);
      ROS_INFO("epgeneral_video_srt pipeline state %s -> %s",
               gst_element_state_get_name(old_state), gst_element_state_get_name(new_state));
    }
    return TRUE;
  }

  static gboolean callerConnecting(GstElement*, GSocketAddress*, const gchar* stream_id,
                                    gpointer user_data) {
    VideoSrtNode* node = static_cast<VideoSrtNode*>(user_data);
    ROS_INFO_STREAM("epgeneral_video_srt caller connecting device=" << node->device_id_
                    << " stream_id=" << (stream_id != nullptr ? stream_id : ""));
    return TRUE;
  }

  static void callerAdded(GstElement*, gint socket, GSocketAddress*, gpointer user_data) {
    VideoSrtNode* node = static_cast<VideoSrtNode*>(user_data);
    ROS_INFO_STREAM("epgeneral_video_srt caller connected device=" << node->device_id_
                    << " socket=" << socket);
  }

  static void callerRemoved(GstElement*, gint socket, GSocketAddress*, gpointer user_data) {
    VideoSrtNode* node = static_cast<VideoSrtNode*>(user_data);
    ROS_INFO_STREAM("epgeneral_video_srt caller disconnected device=" << node->device_id_
                    << " socket=" << socket);
  }

  ros::NodeHandle nh_, pnh_;
  image_transport::ImageTransport image_transport_;
  image_transport::Subscriber image_subscriber_;
  ros::Subscriber compressed_subscriber_;
  ros::WallTimer frame_watchdog_;
  std::string device_id_, device_ip_, image_topic_, image_message_type_, bind_address_;
  int srt_port_, srt_latency_ms_, output_width_, output_height_, framerate_, bitrate_kbps_;
  double frame_timeout_seconds_;
  GstElement* pipeline_;
  GstElement* appsrc_;
  GstElement* srt_sink_;
  GMainLoop* main_loop_;
  guint bus_watch_id_;
  std::thread glib_thread_;
  std::mutex push_mutex_;
  std::uint64_t sequence_;
  ros::WallTime last_frame_time_;
  std::atomic<bool> received_frame_, shutting_down_;
};

int main(int argc, char** argv) {
  ros::init(argc, argv, "epgeneral_video_srt");
  try {
    VideoSrtNode node;
    ros::spin();
    return 0;
  } catch (const std::exception& error) {
    std::fprintf(stderr, "epgeneral_video_srt startup failed: %s\n", error.what());
    std::fflush(stderr);
    ROS_FATAL("epgeneral_video_srt startup failed: %s", error.what());
    return 1;
  }
}
