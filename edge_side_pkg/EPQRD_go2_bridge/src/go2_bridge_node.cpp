#include <algorithm>
#include <array>
#include <cmath>
#include <cctype>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>

#include <diagnostic_msgs/DiagnosticArray.h>
#include <diagnostic_msgs/DiagnosticStatus.h>
#include <diagnostic_msgs/KeyValue.h>
#include <nav_msgs/Odometry.h>
#include <ros/ros.h>
#include <sensor_msgs/BatteryState.h>
#include <sensor_msgs/Imu.h>
#include <std_msgs/Bool.h>
#include <std_msgs/Header.h>
#include <std_msgs/String.h>

#include <unitree/idl/go2/LowState_.hpp>
#include <unitree/idl/go2/SportModeState_.hpp>
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

namespace {

template <typename Values>
bool finiteValues(const Values& values) {
  for (const auto value : values) {
    if (!std::isfinite(static_cast<double>(value))) return false;
  }
  return true;
}

diagnostic_msgs::KeyValue value(const std::string& key, const std::string& text) {
  diagnostic_msgs::KeyValue result;
  result.key = key;
  result.value = text;
  return result;
}

std::string number(double input) {
  std::ostringstream stream;
  stream << input;
  return stream.str();
}

class Go2Bridge {
 public:
  Go2Bridge() : private_nh_("~") {
    std::string shared_device_id;
    private_nh_.param<std::string>("device/id", shared_device_id, "QRD_001");
    private_nh_.param<std::string>("device_id", device_id_, shared_device_id);
    if (device_id_ != shared_device_id) throw std::runtime_error("bridge and shared device IDs do not match");
    private_nh_.param<std::string>("topic_prefix", topic_prefix_, "/qrd");
    private_nh_.param<std::string>("network_interface", interface_, "eth0");
    private_nh_.param("dds_domain_id", domain_id_, 0);
    private_nh_.param<std::string>("topics/low_state", low_topic_, "rt/lowstate");
    private_nh_.param<std::string>("topics/sport_mode_state", sport_topic_, "rt/sportmodestate");
    private_nh_.param<std::string>("frames/odom", odom_frame_, "odom");
    private_nh_.param<std::string>("frames/base", base_frame_, "base_link");
    private_nh_.param("rates/imu_hz", imu_hz_, 100.0);
    private_nh_.param("rates/odometry_hz", odom_hz_, 50.0);
    private_nh_.param("rates/battery_hz", battery_hz_, 1.0);
    private_nh_.param("timeouts/low_state_seconds", low_timeout_, 0.5);
    private_nh_.param("timeouts/sport_mode_seconds", sport_timeout_, 0.5);
    private_nh_.param("covariance/orientation", orientation_covariance_, 0.02);
    private_nh_.param("covariance/angular_velocity", angular_covariance_, 0.02);
    private_nh_.param("covariance/linear_acceleration", acceleration_covariance_, 0.10);
    validate();

    namespace_ = topic_prefix_ + "/" + device_id_;
    battery_pub_ = nh_.advertise<sensor_msgs::BatteryState>(namespace_ + "/battery", 2);
    imu_pub_ = nh_.advertise<sensor_msgs::Imu>(namespace_ + "/imu", 20);
    odom_pub_ = nh_.advertise<nav_msgs::Odometry>(namespace_ + "/odometry", 10);
    mode_pub_ = nh_.advertise<std_msgs::String>(namespace_ + "/robot_mode", 1, true);
    link_pub_ = nh_.advertise<std_msgs::Bool>(namespace_ + "/link/sdk", 1, true);
    heartbeat_pub_ = nh_.advertise<std_msgs::Header>(namespace_ + "/heartbeat", 2);
    diagnostics_pub_ = nh_.advertise<diagnostic_msgs::DiagnosticArray>(namespace_ + "/diagnostics", 2);
  }

  void start() {
    unitree::robot::ChannelFactory::Instance()->Init(domain_id_, interface_);
    low_subscriber_.reset(new unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::LowState_>(low_topic_));
    sport_subscriber_.reset(new unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_>(sport_topic_));
    low_subscriber_->InitChannel(std::bind(&Go2Bridge::onLowState, this, std::placeholders::_1), 1);
    sport_subscriber_->InitChannel(std::bind(&Go2Bridge::onSportState, this, std::placeholders::_1), 1);
    status_timer_ = nh_.createTimer(ros::Duration(1.0), &Go2Bridge::publishStatus, this);
    ROS_INFO_STREAM("epqrd_go2_bridge started device=" << device_id_ << " interface=" << interface_
                    << " namespace=" << namespace_);
  }

 private:
  void validate() {
    if (device_id_.empty() || !std::isalpha(static_cast<unsigned char>(device_id_[0]))) throw std::runtime_error("device_id must start with a letter");
    for (const char character : device_id_) {
      if (!std::isalnum(static_cast<unsigned char>(character)) && character != '_') throw std::runtime_error("device_id is not a ROS namespace segment");
    }
    if (topic_prefix_.empty() || topic_prefix_[0] != '/') throw std::runtime_error("topic_prefix must be absolute");
    if (interface_.empty()) throw std::runtime_error("network_interface is required");
    if (imu_hz_ <= 0 || imu_hz_ > 500 || odom_hz_ <= 0 || odom_hz_ > 200 || battery_hz_ <= 0 || battery_hz_ > 10)
      throw std::runtime_error("publish rates are out of range");
    if (low_timeout_ <= 0 || sport_timeout_ <= 0) throw std::runtime_error("timeouts must be positive");
  }

  void onLowState(const void* raw) {
    const auto state = *static_cast<const unitree_go::msg::dds_::LowState_*>(raw);
    const ros::Time now = ros::Time::now();
    bool first = false;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      first = last_low_.isZero();
      last_low_ = now;
      last_voltage_ = state.power_v();
      last_current_ = state.power_a();
      last_soc_ = state.bms_state().soc();
    }
    if (first) ROS_INFO("received first Unitree LowState frame");
    if ((now - last_battery_publish_).toSec() < 1.0 / battery_hz_) return;
    if (!std::isfinite(state.power_v()) || !std::isfinite(state.power_a())) return;
    sensor_msgs::BatteryState battery;
    battery.header.stamp = now;
    battery.header.frame_id = base_frame_;
    battery.voltage = state.power_v();
    battery.current = state.power_a();
    battery.percentage = std::max(0.0f, std::min(1.0f, static_cast<float>(state.bms_state().soc()) / 100.0f));
    battery.power_supply_status = sensor_msgs::BatteryState::POWER_SUPPLY_STATUS_UNKNOWN;
    battery.power_supply_health = sensor_msgs::BatteryState::POWER_SUPPLY_HEALTH_UNKNOWN;
    battery.power_supply_technology = sensor_msgs::BatteryState::POWER_SUPPLY_TECHNOLOGY_LION;
    battery.present = true;
    battery_pub_.publish(battery);
    last_battery_publish_ = now;
  }

  void onSportState(const void* raw) {
    const auto state = *static_cast<const unitree_go::msg::dds_::SportModeState_*>(raw);
    const ros::Time now = ros::Time::now();
    bool first = false;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      first = last_sport_.isZero();
      last_sport_ = now;
      last_error_code_ = state.error_code();
      last_mode_ = state.mode();
      last_gait_ = state.gait_type();
    }
    if (first) ROS_INFO("received first Unitree SportModeState frame");
    publishMode(state);

    const auto quaternion = state.imu_state().quaternion();
    const auto gyroscope = state.imu_state().gyroscope();
    const auto acceleration = state.imu_state().accelerometer();
    const double norm = std::sqrt(quaternion[0] * quaternion[0] + quaternion[1] * quaternion[1] +
                                  quaternion[2] * quaternion[2] + quaternion[3] * quaternion[3]);
    if (finiteValues(quaternion) && finiteValues(gyroscope) && finiteValues(acceleration) && norm > 1e-6 &&
        (now - last_imu_publish_).toSec() >= 1.0 / imu_hz_) {
      sensor_msgs::Imu imu;
      imu.header.stamp = now;
      imu.header.frame_id = base_frame_;
      imu.orientation.w = quaternion[0] / norm;
      imu.orientation.x = quaternion[1] / norm;
      imu.orientation.y = quaternion[2] / norm;
      imu.orientation.z = quaternion[3] / norm;
      imu.angular_velocity.x = gyroscope[0]; imu.angular_velocity.y = gyroscope[1]; imu.angular_velocity.z = gyroscope[2];
      imu.linear_acceleration.x = acceleration[0]; imu.linear_acceleration.y = acceleration[1]; imu.linear_acceleration.z = acceleration[2];
      imu.orientation_covariance[0] = imu.orientation_covariance[4] = imu.orientation_covariance[8] = orientation_covariance_;
      imu.angular_velocity_covariance[0] = imu.angular_velocity_covariance[4] = imu.angular_velocity_covariance[8] = angular_covariance_;
      imu.linear_acceleration_covariance[0] = imu.linear_acceleration_covariance[4] = imu.linear_acceleration_covariance[8] = acceleration_covariance_;
      imu_pub_.publish(imu);
      last_imu_publish_ = now;
    }

    const auto position = state.position();
    const auto velocity = state.velocity();
    if (finiteValues(position) && finiteValues(velocity) && finiteValues(quaternion) && norm > 1e-6 &&
        (now - last_odom_publish_).toSec() >= 1.0 / odom_hz_) {
      nav_msgs::Odometry odometry;
      odometry.header.stamp = now;
      odometry.header.frame_id = odom_frame_;
      odometry.child_frame_id = base_frame_;
      odometry.pose.pose.position.x = position[0]; odometry.pose.pose.position.y = position[1]; odometry.pose.pose.position.z = position[2];
      odometry.pose.pose.orientation.w = quaternion[0] / norm;
      odometry.pose.pose.orientation.x = quaternion[1] / norm;
      odometry.pose.pose.orientation.y = quaternion[2] / norm;
      odometry.pose.pose.orientation.z = quaternion[3] / norm;
      odometry.twist.twist.linear.x = velocity[0]; odometry.twist.twist.linear.y = velocity[1]; odometry.twist.twist.linear.z = velocity[2];
      odometry.twist.twist.angular.z = state.yaw_speed();
      odom_pub_.publish(odometry);
      last_odom_publish_ = now;
    }
  }

  void publishMode(const unitree_go::msg::dds_::SportModeState_& state) {
    const std::string mode = "mode=" + std::to_string(state.mode()) + ",gait=" +
                             std::to_string(state.gait_type()) + ",error=" + std::to_string(state.error_code());
    if (mode == published_mode_) return;
    std_msgs::String message;
    message.data = mode;
    mode_pub_.publish(message);
    published_mode_ = mode;
    ROS_INFO_STREAM("Go2 state changed " << mode);
  }

  void publishStatus(const ros::TimerEvent&) {
    const ros::Time now = ros::Time::now();
    ros::Time low_stamp, sport_stamp;
    float voltage, current;
    uint8_t soc;
    uint32_t error;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      low_stamp = last_low_; sport_stamp = last_sport_;
      voltage = last_voltage_; current = last_current_; soc = last_soc_; error = last_error_code_;
    }
    const double low_age = low_stamp.isZero() ? -1.0 : (now - low_stamp).toSec();
    const double sport_age = sport_stamp.isZero() ? -1.0 : (now - sport_stamp).toSec();
    const bool online = low_age >= 0 && low_age <= low_timeout_ && sport_age >= 0 && sport_age <= sport_timeout_;
    std_msgs::Bool link; link.data = online; link_pub_.publish(link);
    std_msgs::Header heartbeat; heartbeat.seq = heartbeat_sequence_++; heartbeat.stamp = now; heartbeat.frame_id = base_frame_;
    heartbeat_pub_.publish(heartbeat);

    diagnostic_msgs::DiagnosticArray report;
    report.header.stamp = now;
    diagnostic_msgs::DiagnosticStatus status;
    status.name = "epqrd_go2_bridge/sdk";
    status.hardware_id = device_id_;
    status.level = online && error == 0 ? diagnostic_msgs::DiagnosticStatus::OK :
                   (online ? diagnostic_msgs::DiagnosticStatus::WARN : diagnostic_msgs::DiagnosticStatus::ERROR);
    status.message = !online ? "Unitree DDS state is stale" : (error == 0 ? "Unitree DDS state is healthy" : "SportModeState reports an error");
    status.values = {value("low_state_age_seconds", number(low_age)), value("sport_state_age_seconds", number(sport_age)),
                     value("battery_voltage", number(voltage)), value("battery_current", number(current)),
                     value("battery_percentage", number(soc)), value("error_code", std::to_string(error))};
    report.status.push_back(status);
    diagnostics_pub_.publish(report);
    if (online != last_online_) {
      if (online) {
        ROS_INFO("Unitree SDK link online low_age=%.3f sport_age=%.3f", low_age, sport_age);
      } else {
        ROS_WARN("Unitree SDK link offline low_age=%.3f sport_age=%.3f", low_age, sport_age);
      }
      last_online_ = online;
    }
    ROS_INFO_STREAM_THROTTLE(1.0, "heartbeat device=" << device_id_ << " sequence=" << heartbeat.seq << " sdk_link=" << online);
  }

  ros::NodeHandle nh_, private_nh_;
  ros::Publisher battery_pub_, imu_pub_, odom_pub_, mode_pub_, link_pub_, heartbeat_pub_, diagnostics_pub_;
  ros::Timer status_timer_;
  std::unique_ptr<unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::LowState_>> low_subscriber_;
  std::unique_ptr<unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_>> sport_subscriber_;
  std::mutex mutex_;
  ros::Time last_low_, last_sport_, last_battery_publish_, last_imu_publish_, last_odom_publish_;
  std::string device_id_, topic_prefix_, namespace_, interface_, low_topic_, sport_topic_, odom_frame_, base_frame_, published_mode_;
  int domain_id_ = 0;
  double imu_hz_ = 100, odom_hz_ = 50, battery_hz_ = 1, low_timeout_ = 0.5, sport_timeout_ = 0.5;
  double orientation_covariance_ = 0.02, angular_covariance_ = 0.02, acceleration_covariance_ = 0.10;
  float last_voltage_ = NAN, last_current_ = NAN;
  uint8_t last_soc_ = 0, last_mode_ = 0, last_gait_ = 0;
  uint32_t last_error_code_ = 0, heartbeat_sequence_ = 0;
  bool last_online_ = false;
};

}  // namespace

int main(int argc, char** argv) {
  ros::init(argc, argv, "epqrd_go2_bridge");
  try {
    Go2Bridge bridge;
    bridge.start();
    ros::spin();
  } catch (const std::exception& error) {
    ROS_FATAL("epqrd_go2_bridge startup failed: %s", error.what());
    return 1;
  }
  ROS_INFO("epqrd_go2_bridge stopped");
  return 0;
}
