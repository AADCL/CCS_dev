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

#include <epqrd_go2_bridge/BmsState.h>
#include <epqrd_go2_bridge/ImuState.h>
#include <epqrd_go2_bridge/LowStateFootForce.h>
#include <epqrd_go2_bridge/LowStateInfo.h>
#include <epqrd_go2_bridge/MotorStateArray.h>
#include <epqrd_go2_bridge/ObstacleRanges.h>
#include <epqrd_go2_bridge/PathPointArray.h>
#include <epqrd_go2_bridge/SportModeFootState.h>
#include <epqrd_go2_bridge/SportModeKinematics.h>
#include <epqrd_go2_bridge/SportModeStatus.h>
#include <epqrd_go2_bridge/WirelessRemote.h>

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
    private_nh_.param("rates/low_state_hz", low_state_hz_, 100.0);
    private_nh_.param("rates/sport_mode_hz", sport_mode_hz_, 50.0);
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
    low_info_pub_ = nh_.advertise<epqrd_go2_bridge::LowStateInfo>(namespace_ + "/low_state/info", 5);
    low_imu_pub_ = nh_.advertise<epqrd_go2_bridge::ImuState>(namespace_ + "/low_state/imu", 5);
    motors_pub_ = nh_.advertise<epqrd_go2_bridge::MotorStateArray>(namespace_ + "/low_state/motors", 5);
    bms_pub_ = nh_.advertise<epqrd_go2_bridge::BmsState>(namespace_ + "/low_state/bms", 5);
    low_foot_pub_ = nh_.advertise<epqrd_go2_bridge::LowStateFootForce>(namespace_ + "/low_state/foot_force", 5);
    remote_pub_ = nh_.advertise<epqrd_go2_bridge::WirelessRemote>(namespace_ + "/low_state/wireless_remote", 5);
    sport_status_pub_ = nh_.advertise<epqrd_go2_bridge::SportModeStatus>(namespace_ + "/sport_mode/status", 5);
    sport_imu_pub_ = nh_.advertise<epqrd_go2_bridge::ImuState>(namespace_ + "/sport_mode/imu", 5);
    kinematics_pub_ = nh_.advertise<epqrd_go2_bridge::SportModeKinematics>(namespace_ + "/sport_mode/kinematics", 5);
    obstacles_pub_ = nh_.advertise<epqrd_go2_bridge::ObstacleRanges>(namespace_ + "/sport_mode/obstacle_ranges", 5);
    sport_feet_pub_ = nh_.advertise<epqrd_go2_bridge::SportModeFootState>(namespace_ + "/sport_mode/feet", 5);
    path_pub_ = nh_.advertise<epqrd_go2_bridge::PathPointArray>(namespace_ + "/sport_mode/path", 5);
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
    if (imu_hz_ <= 0 || imu_hz_ > 500 || odom_hz_ <= 0 || odom_hz_ > 200 || battery_hz_ <= 0 || battery_hz_ > 10 ||
        low_state_hz_ <= 0 || low_state_hz_ > 500 || sport_mode_hz_ <= 0 || sport_mode_hz_ > 500)
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
    if ((now - last_low_raw_publish_).toSec() >= 1.0 / low_state_hz_) {
      publishLowState(state, now);
      last_low_raw_publish_ = now;
    }
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
    if ((now - last_sport_raw_publish_).toSec() >= 1.0 / sport_mode_hz_) {
      publishSportState(state, now);
      last_sport_raw_publish_ = now;
    }
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

  void publishLowState(const unitree_go::msg::dds_::LowState_& state, const ros::Time& now) {
    std_msgs::Header header;
    header.seq = low_sequence_++;
    header.stamp = now;
    header.frame_id = base_frame_;

    epqrd_go2_bridge::LowStateInfo info;
    info.header = header;
    std::copy(state.head().begin(), state.head().end(), info.head.begin());
    info.level_flag = state.level_flag(); info.frame_reserve = state.frame_reserve();
    std::copy(state.sn().begin(), state.sn().end(), info.sn.begin());
    std::copy(state.version().begin(), state.version().end(), info.version.begin());
    info.bandwidth = state.bandwidth(); info.tick = state.tick(); info.bit_flag = state.bit_flag();
    info.adc_reel = state.adc_reel(); info.temperature_ntc1 = state.temperature_ntc1();
    info.temperature_ntc2 = state.temperature_ntc2(); info.power_v = state.power_v(); info.power_a = state.power_a();
    std::copy(state.fan_frequency().begin(), state.fan_frequency().end(), info.fan_frequency.begin());
    info.reserve = state.reserve(); info.crc = state.crc();
    low_info_pub_.publish(info);

    low_imu_pub_.publish(makeImuState(state.imu_state(), header));

    epqrd_go2_bridge::MotorStateArray motors;
    motors.header = header;
    for (std::size_t index = 0; index < state.motor_state().size(); ++index) {
      const auto& source = state.motor_state()[index];
      auto& target = motors.motors[index];
      target.mode = source.mode(); target.q = source.q(); target.dq = source.dq(); target.ddq = source.ddq();
      target.tau_est = source.tau_est(); target.q_raw = source.q_raw(); target.dq_raw = source.dq_raw();
      target.ddq_raw = source.ddq_raw(); target.temperature = source.temperature(); target.lost = source.lost();
      std::copy(source.reserve().begin(), source.reserve().end(), target.reserve.begin());
    }
    motors_pub_.publish(motors);

    const auto& source_bms = state.bms_state();
    epqrd_go2_bridge::BmsState bms;
    bms.header = header; bms.version_high = source_bms.version_high(); bms.version_low = source_bms.version_low();
    bms.status = source_bms.status(); bms.soc = source_bms.soc(); bms.current = source_bms.current(); bms.cycle = source_bms.cycle();
    std::copy(source_bms.bq_ntc().begin(), source_bms.bq_ntc().end(), bms.bq_ntc.begin());
    std::copy(source_bms.mcu_ntc().begin(), source_bms.mcu_ntc().end(), bms.mcu_ntc.begin());
    std::copy(source_bms.cell_vol().begin(), source_bms.cell_vol().end(), bms.cell_vol.begin());
    bms_pub_.publish(bms);

    epqrd_go2_bridge::LowStateFootForce force;
    force.header = header;
    std::copy(state.foot_force().begin(), state.foot_force().end(), force.measured.begin());
    std::copy(state.foot_force_est().begin(), state.foot_force_est().end(), force.estimated.begin());
    low_foot_pub_.publish(force);

    epqrd_go2_bridge::WirelessRemote remote;
    remote.header = header;
    std::copy(state.wireless_remote().begin(), state.wireless_remote().end(), remote.data.begin());
    remote_pub_.publish(remote);
  }

  epqrd_go2_bridge::ImuState makeImuState(const unitree_go::msg::dds_::IMUState_& source,
                                           const std_msgs::Header& header) const {
    epqrd_go2_bridge::ImuState target;
    target.header = header;
    std::copy(source.quaternion().begin(), source.quaternion().end(), target.quaternion.begin());
    std::copy(source.gyroscope().begin(), source.gyroscope().end(), target.gyroscope.begin());
    std::copy(source.accelerometer().begin(), source.accelerometer().end(), target.accelerometer.begin());
    std::copy(source.rpy().begin(), source.rpy().end(), target.rpy.begin());
    target.temperature = source.temperature();
    return target;
  }

  void publishSportState(const unitree_go::msg::dds_::SportModeState_& state, const ros::Time& now) {
    std_msgs::Header header;
    header.seq = sport_sequence_++;
    header.stamp = now;
    header.frame_id = base_frame_;

    epqrd_go2_bridge::SportModeStatus status;
    status.header = header;
    if (state.stamp().sec() >= 0 && state.stamp().nanosec() < 1000000000U)
      status.source_stamp = ros::Time(static_cast<uint32_t>(state.stamp().sec()), state.stamp().nanosec());
    status.error_code = state.error_code(); status.mode = state.mode(); status.progress = state.progress();
    status.gait_type = state.gait_type(); status.foot_raise_height = state.foot_raise_height(); status.body_height = state.body_height();
    sport_status_pub_.publish(status);

    sport_imu_pub_.publish(makeImuState(state.imu_state(), header));

    epqrd_go2_bridge::SportModeKinematics kinematics;
    kinematics.header = header;
    std::copy(state.position().begin(), state.position().end(), kinematics.position.begin());
    std::copy(state.velocity().begin(), state.velocity().end(), kinematics.velocity.begin());
    kinematics.yaw_speed = state.yaw_speed();
    kinematics_pub_.publish(kinematics);

    epqrd_go2_bridge::ObstacleRanges obstacles;
    obstacles.header = header;
    std::copy(state.range_obstacle().begin(), state.range_obstacle().end(), obstacles.ranges.begin());
    obstacles_pub_.publish(obstacles);

    epqrd_go2_bridge::SportModeFootState feet;
    feet.header = header;
    std::copy(state.foot_force().begin(), state.foot_force().end(), feet.force.begin());
    std::copy(state.foot_position_body().begin(), state.foot_position_body().end(), feet.position_body.begin());
    std::copy(state.foot_speed_body().begin(), state.foot_speed_body().end(), feet.speed_body.begin());
    sport_feet_pub_.publish(feet);

    epqrd_go2_bridge::PathPointArray path;
    path.header = header;
    for (std::size_t index = 0; index < state.path_point().size(); ++index) {
      const auto& source = state.path_point()[index];
      auto& target = path.points[index];
      target.t_from_start = source.t_from_start(); target.x = source.x(); target.y = source.y(); target.yaw = source.yaw();
      target.vx = source.vx(); target.vy = source.vy(); target.vyaw = source.vyaw();
    }
    path_pub_.publish(path);
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
  ros::Publisher low_info_pub_, low_imu_pub_, motors_pub_, bms_pub_, low_foot_pub_, remote_pub_;
  ros::Publisher sport_status_pub_, sport_imu_pub_, kinematics_pub_, obstacles_pub_, sport_feet_pub_, path_pub_;
  ros::Timer status_timer_;
  std::unique_ptr<unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::LowState_>> low_subscriber_;
  std::unique_ptr<unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_>> sport_subscriber_;
  std::mutex mutex_;
  ros::Time last_low_, last_sport_, last_battery_publish_, last_imu_publish_, last_odom_publish_;
  ros::Time last_low_raw_publish_, last_sport_raw_publish_;
  std::string device_id_, topic_prefix_, namespace_, interface_, low_topic_, sport_topic_, odom_frame_, base_frame_, published_mode_;
  int domain_id_ = 0;
  double imu_hz_ = 100, odom_hz_ = 50, battery_hz_ = 1, low_state_hz_ = 100, sport_mode_hz_ = 50;
  double low_timeout_ = 0.5, sport_timeout_ = 0.5;
  double orientation_covariance_ = 0.02, angular_covariance_ = 0.02, acceleration_covariance_ = 0.10;
  float last_voltage_ = NAN, last_current_ = NAN;
  uint8_t last_soc_ = 0, last_mode_ = 0, last_gait_ = 0;
  uint32_t last_error_code_ = 0, heartbeat_sequence_ = 0, low_sequence_ = 0, sport_sequence_ = 0;
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
