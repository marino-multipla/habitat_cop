
"""Tutorial to show how to use Spot's arm.
"""
from __future__ import print_function

import argparse
import sys
import time

from google.protobuf import wrappers_pb2
import bosdyn.api.gripper_command_pb2
import bosdyn.client
import bosdyn.client.lease
import bosdyn.client.util
from bosdyn.client import math_helpers
from bosdyn.api import geometry_pb2, power_pb2, robot_state_pb2, arm_command_pb2, synchronized_command_pb2, robot_command_pb2
from bosdyn.client.frame_helpers import GRAV_ALIGNED_BODY_FRAME_NAME, ODOM_FRAME_NAME, get_a_tform_b
from bosdyn.client.robot_command import (RobotCommandBuilder, RobotCommandClient,
                                         block_until_arm_arrives, blocking_stand)
from bosdyn.client.robot_state import RobotStateClient


def lock_hand_in_body_and_move(config):
    """A simple example of using the Boston Dynamics API to command Spot's arm.
    Spot will set an arm pose, ,will freeze the arm pose, then it will move forward by 2 meters.
    The expected result will be spot is moving with the hand locked in the body reference position.
    It is the same behavior as with the remote controller by activating the LOCK_HAND_IN_BODY option.
    """

    # See hello_spot.py for an explanation of these lines.
    bosdyn.client.util.setup_logging(config.verbose)

    sdk = bosdyn.client.create_standard_sdk('HelloSpotClient')
    robot = sdk.create_robot(config.hostname)
    bosdyn.client.util.authenticate(robot)
    robot.time_sync.wait_for_sync()

    assert robot.has_arm(), "Robot requires an arm to run this example."

    # Verify the robot is not estopped and that an external application has registered and holds
    # an estop endpoint.
    assert not robot.is_estopped(), "Robot is estopped. Please use an external E-Stop client, " \
                                    "such as the estop SDK example, to configure E-Stop."

    robot_state_client = robot.ensure_client(RobotStateClient.default_service_name)

    lease_client = robot.ensure_client(bosdyn.client.lease.LeaseClient.default_service_name)
    with bosdyn.client.lease.LeaseKeepAlive(lease_client, must_acquire=True, return_at_exit=True):
        # Now, we are ready to power on the robot. This call will block until the power
        # is on. Commands would fail if this did not happen. We can also check that the robot is
        # powered at any point.
        robot.logger.info("Powering on robot... This may take a several seconds.")
        robot.power_on(timeout_sec=20)
        assert robot.is_powered_on(), "Robot power on failed."
        robot.logger.info("Robot powered on.")

        # Tell the robot to stand up. The command service is used to issue commands to a robot.
        # The set of valid commands for a robot depends on hardware configuration. See
        # RobotCommandBuilder for more detailed examples on command building. The robot
        # command service requires timesync between the robot and the client.
        robot.logger.info("Commanding robot to stand...")
        command_client = robot.ensure_client(RobotCommandClient.default_service_name)
        blocking_stand(command_client, timeout_sec=10)
        robot.logger.info("Robot standing.")

        # Move the arm to a spot in front of the robot, and open the gripper.

        # Make the arm pose RobotCommand
        # Build a position to move the arm to (in meters, relative to and expressed in the gravity aligned body frame).
        x = 0.75
        y = 0
        z = 0.65
        hand_ewrt_flat_body = geometry_pb2.Vec3(x=x, y=y, z=z)

        # Rotation as a quaternion
        qw = 1
        qx = 0
        qy = 0
        qz = 0
        flat_body_Q_hand = geometry_pb2.Quaternion(w=qw, x=qx, y=qy, z=qz)

        flat_body_T_hand = geometry_pb2.SE3Pose(position=hand_ewrt_flat_body,
                                                rotation=flat_body_Q_hand)

        robot_state = robot_state_client.get_robot_state()
        odom_T_flat_body = get_a_tform_b(robot_state.kinematic_state.transforms_snapshot,
                                         ODOM_FRAME_NAME, GRAV_ALIGNED_BODY_FRAME_NAME)

        odom_T_hand = odom_T_flat_body * math_helpers.SE3Pose.from_obj(flat_body_T_hand)

        # duration in seconds
        seconds = 2

        arm_command = RobotCommandBuilder.arm_pose_command(
            odom_T_hand.x, odom_T_hand.y, odom_T_hand.z, odom_T_hand.rot.w, odom_T_hand.rot.x,
            odom_T_hand.rot.y, odom_T_hand.rot.z, ODOM_FRAME_NAME, seconds)

        # Make the open gripper RobotCommand
        gripper_command = RobotCommandBuilder.claw_gripper_open_fraction_command(1.0)

        # Combine the arm and gripper commands into one RobotCommand
        command = RobotCommandBuilder.build_synchro_command(gripper_command, arm_command)

        # Send the request
        cmd_id = command_client.robot_command(command)
        robot.logger.info('Moving arm to position 1.')

        # Wait until the arm arrives at the goal.
        block_until_arm_arrives_with_prints(robot, command_client, cmd_id)

        # Lock arm in body
        traj_point = arm_command_pb2.ArmJointTrajectoryPoint()
        max_vel = wrappers_pb2.DoubleValue(value=5.0)
        max_acc = wrappers_pb2.DoubleValue(value=20)
        arm_joint_traj = arm_command_pb2.ArmJointTrajectory(points=[traj_point], maximum_velocity=max_vel,
                                                            maximum_acceleration=max_acc)
        joint_move_command = arm_command_pb2.ArmJointMoveCommand.Request(trajectory=arm_joint_traj)
        arm_command = arm_command_pb2.ArmCommand.Request(arm_joint_move_command=joint_move_command)
        sync_arm = synchronized_command_pb2.SynchronizedCommand.Request(arm_command=arm_command)
        arm_sync_robot_cmd = robot_command_pb2.RobotCommand(synchronized_command=sync_arm)
        command = RobotCommandBuilder.build_synchro_command(arm_sync_robot_cmd)
        # Send the request
        cmd_id = command_client.robot_command(command)
        robot.logger.info('Moving arm to Freeze.')
        time.sleep(1)

        # Move forward 2 meters
        VELOCITY_BASE_SPEED = 0.5  # m/s
        VELOCITY_BASE_ANGULAR = 0.8  # rad/sec
        VELOCITY_CMD_DURATION = 4  # seconds
        COMMAND_INPUT_RATE = 0.1
        move_command = RobotCommandBuilder.synchro_velocity_command(v_x=VELOCITY_BASE_SPEED, v_y=0.0, v_rot=0.0)

        command_client.robot_command(command=move_command, end_time_secs=time.time() + VELOCITY_CMD_DURATION)

        robot.logger.info('Done.')

        time.sleep(10)

        # Power the robot off. By specifying "cut_immediately=False", a safe power off command
        # is issued to the robot. This will attempt to sit the robot before powering off.
        robot.power_off(cut_immediately=False, timeout_sec=20)
        assert not robot.is_powered_on(), "Robot power off failed."
        robot.logger.info("Robot safely powered off.")


def block_until_arm_arrives_with_prints(robot, command_client, cmd_id):
    """Block until the arm arrives at the goal and print the distance remaining.
        Note: a version of this function is available as a helper in robot_command
        without the prints.
    """
    while True:
        feedback_resp = command_client.robot_command_feedback(cmd_id)
        robot.logger.info(
            'Distance to go: ' +
            '{:.2f} meters'.format(feedback_resp.feedback.synchronized_feedback.arm_command_feedback
                                   .arm_cartesian_feedback.measured_pos_distance_to_goal) +
            ', {:.2f} radians'.format(
                feedback_resp.feedback.synchronized_feedback.arm_command_feedback.
                arm_cartesian_feedback.measured_rot_distance_to_goal))

        if feedback_resp.feedback.synchronized_feedback.arm_command_feedback.arm_cartesian_feedback.status == arm_command_pb2.ArmCartesianCommand.Feedback.STATUS_TRAJECTORY_COMPLETE:
            robot.logger.info('Move complete.')
            break
        time.sleep(0.1)


def main(argv):
    """Command line interface."""
    parser = argparse.ArgumentParser()
    bosdyn.client.util.add_base_arguments(parser)
    options = parser.parse_args(argv)
    try:
        lock_hand_in_body_and_move(options)
        return True
    except Exception as exc:  # pylint: disable=broad-except
        logger = bosdyn.client.util.get_logger()
        logger.exception("Threw an exception")
        return False


if __name__ == '__main__':
    if not main(sys.argv[1:]):
        sys.exit(1)
