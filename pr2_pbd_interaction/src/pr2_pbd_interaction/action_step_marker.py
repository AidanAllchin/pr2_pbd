"""Represents a single 3D marker (in RViz) for visualizing the steps of
an action."""

# ######################################################################
# Imports
# ######################################################################

# Core ROS imports come first.
import roslib
roslib.load_manifest('pr2_pbd_interaction')
import rospy

# 3rd party
import numpy

# ROS builtins
from geometry_msgs.msg import Quaternion, Vector3, Point, Pose
from std_msgs.msg import Header, ColorRGBA
import tf

# ROS 3rd party
from interactive_markers.interactive_marker_server import (
    InteractiveMarkerServer
)
from interactive_markers.menu_handler import MenuHandler
from visualization_msgs.msg import (Marker, InteractiveMarker,
                                    InteractiveMarkerControl,
                                    InteractiveMarkerFeedback)

# Local
from arms import Arms
from pr2_arm_control.msg import Side, GripperState
from pr2_pbd_interaction.msg import (ActionStep, ArmState, Landmark)
import world
from world import World

# ######################################################################
# Module level constants
# ######################################################################

DEFAULT_OFFSET = 0.09

# Marker options
# --------------
# Colors
COLOR_TRAJ_ENDPOINT_SPHERES = ColorRGBA(1.0, 0.5, 0.0, 0.8)
COLOR_TRAJ_STEP_SPHERES = ColorRGBA(0.8, 0.4, 0.0, 0.8)
COLOR_OBJ_REF_ARROW = ColorRGBA(1.0, 0.8, 0.2, 0.5)
COLOR_STEP_TEXT = ColorRGBA(0.0, 0.0, 0.0, 0.5)
COLOR_MESH_REACHABLE = ColorRGBA(1.0, 0.5, 0.0, 0.6)
COLOR_MESH_UNREACHABLE = ColorRGBA(0.5, 0.5, 0.5, 0.6)

# Scales
SCALE_TRAJ_STEP_SPHERES = Vector3(0.02, 0.02, 0.02)
SCALE_OBJ_REF_ARROW = Vector3(0.02, 0.03, 0.04)
SCALE_STEP_TEXT = Vector3(0, 0, 0.03)

# Gripper mesh related
ANGLE_GRIPPER_OPEN = 28 * numpy.pi / 180.0
ANGLE_GRIPPER_CLOSED = 0.0
STR_MESH_GRIPPER_FOLDER = 'package://pr2_description/meshes/gripper_v0/'
STR_GRIPPER_PALM_FILE = STR_MESH_GRIPPER_FOLDER + 'gripper_palm.dae'
STR_GRIPPER_FINGER_FILE = STR_MESH_GRIPPER_FOLDER + 'l_finger.dae'
STR_GRIPPER_FINGERTIP_FILE = STR_MESH_GRIPPER_FOLDER + 'l_finger_tip.dae'

# Right-click menu.
MENU_OPTIONS = {
    'ref': 'Reference frame',
    'move_here': 'Move arm here',
    'move_current': 'Move to current arm pose',
    'del': 'Delete',
}

# Offets to maintain globally-unique IDs but with new sets of objects.
# Each action step marker has a unique ID, and this allows each to
# have a matching unique id for trajectories, text, etc. Assumes we'll
# have < 1k steps.
ID_OFFSET_REF_ARROW = 1000
ID_OFFSET_TRAJ_FIRST = 2000
ID_OFFSET_TRAJ_LAST = 3000

# Other
TRAJ_MARKER_LIFETIME = rospy.Duration(2)
TEXT_Z_OFFSET = 0.1
INT_MARKER_SCALE = 0.2
TRAJ_ENDPOINT_SCALE = 0.05

# ROS topics, etc.
# ----------------
# Namespace for interactive marker server.
TOPIC_IM_SERVER = 'programmed_actions'

# We might want to refactor this even further, as it's used throughout
# the code.
BASE_LINK = 'base_link'

# Other options
# --------------
ARM_NAMES = ['right', 'left']

# ######################################################################
# Classes
# ######################################################################


class ActionStepMarker:
    """Marker for visualizing the steps of an action."""

    _im_server = None
    _offset = DEFAULT_OFFSET
    _ref_object_list = None
    _ref_names = None
    _marker_click_cb = None

    def __init__(self, world, step_number, arm_index, action_step,
                 marker_click_cb):
        """
        Args:
            world (World): The world object.
            step_number (int): The 1-based index of the step.
            arm_index (int): Side.RIGHT or Side.LEFT
            action_step (ActionStep): The action step this marker marks.
            marker_click_cb (function(int,bool)): The function to call
                when a marker is clicked. Pass the uid of the marker
                (as calculated by get_uid(...) as well as whether it's
                selected.
        """
        if ActionStepMarker._im_server is None:
            im_server = InteractiveMarkerServer(TOPIC_IM_SERVER, q_size=5)
            ActionStepMarker._im_server = im_server

        self._world = world

        self.action_step = action_step
        self.arm_index = arm_index
        self.arm_name = ARM_NAMES[arm_index]
        self.step_number = step_number
        self.is_requested = False
        self.is_deleted = False
        self.is_control_visible = False
        self.is_edited = False
        self.has_object = False

        self._sub_entries = None
        self._menu_options = None
        self._side_refs = []
        self._menu_handler = None
        self._prev_is_reachable = None
        ActionStepMarker._marker_click_cb = marker_click_cb

    # ##################################################################
    # Static methods: Public (API)
    # ##################################################################

    @staticmethod
    def calc_uid(arm_index, step_number):
        """Returns a unique id of the marker of the arm_index arm with
        step_number step.

        Args:
            arm_index (int): Side.RIGHT or Side.LEFT
            step_number (int): The number of the step.

        Returns:
            int: A number that is unique given the step number and arm
                index.
        """
        return len(ARM_NAMES) * step_number + arm_index

    # ##################################################################
    # Static methods: Internal ("private")
    # ##################################################################

    @staticmethod
    def _make_sphere_marker(uid, pose, frame_id, radius):
        """Creates and returns a sphere marker.

        Args:
            uid (int): The id to use for the marker.
            pose (Pose): The pose of the marker.
            frame_id (str): Frame the marker is associated with. (See
                std_msgs/Header.msg for more info.)
            radius (float): Amount to scale the marker by. Scales in all
                directions (x, y, and z).

        Returns:
            Marker
        """
        return Marker(type=Marker.SPHERE,
                      id=uid,
                      lifetime=rospy.Duration(2),
                      scale=Vector3(radius, radius, radius),
                      pose=pose,
                      header=Header(frame_id=frame_id),
                      color=COLOR_TRAJ_ENDPOINT_SPHERES)

    @staticmethod
    def _offset_pose(pose, constant=1):
        """Offsets the world pose for visualization.

        Args:
            pose (Pose): The pose to offset.
            constant (int, optional): How much to scale the set offset
                by (scales ActionStepMarker._offset). Defaults to 1.

        Returns:
            Pose: The offset pose.
        """
        transform = world.get_matrix_from_pose(pose)
        offset_array = [constant * ActionStepMarker._offset, 0, 0]
        offset_transform = tf.transformations.translation_matrix(offset_array)
        hand_transform = tf.transformations.concatenate_matrices(
            transform, offset_transform)
        return world.get_pose_from_transform(hand_transform)

    # ##################################################################
    # Instance methods: Public (API)
    # ##################################################################

    def get_uid(self):
        """Returns a unique id for this marker.

        Returns:
            int: A number that is unique given the step number and arm
                index.
        """
        return ActionStepMarker.calc_uid(self.arm_index, self.step_number)

    def decrease_id(self):
        """Reduces the step index of the marker."""
        self.step_number -= 1
        self._update_menu()

    def update_ref_frames(self, ref_frame_list):
        """Updates and re-assigns coordinate frames when the world changes.

        Args:
            ref_frame_list ([Landmark]): List of Landmark.msg objects, the
                reference frames of the system.
        """
        # There is a new list of objects. If the current frame is
        # relative (already assigned to an object) we need to figure out
        # the correspondences.

        ActionStepMarker._ref_object_list = ref_frame_list
        ActionStepMarker._ref_object_list += World.side_refs
        arm_pose = self.get_target()
        if arm_pose.refFrame == ArmState.OBJECT:
            prev_ref_obj = arm_pose.refFrameLandmark
            new_ref_obj = world.get_most_similar_obj(prev_ref_obj,
                                                     ref_frame_list)
            if new_ref_obj is not None:
                self.has_object = True
                arm_pose.refFrameLandmark = new_ref_obj
            else:
                self.has_object = False

        # Re-populate cached list of reference names.
        ActionStepMarker._ref_names = [BASE_LINK]
        for obj in ActionStepMarker._ref_object_list:
            ActionStepMarker._ref_names.append(obj.name)

        self._update_menu()

    def destroy(self):
        """Removes marker from the world."""
        ActionStepMarker._im_server.erase(self._get_name())
        ActionStepMarker._im_server.applyChanges()

    def update_pose(self, new_arm_state):
        """Changes the pose of the action step to that given by new_arm_state.

        new_arm_state may specify the pose in the base frame or in the frame of
        a landmark. However, it will be transformed into the frame of this
        action step.

        Args:
            new_pose (Pose)
        """
        if self.action_step.type == ActionStep.ARM_TARGET:
            t = self.action_step.armTarget
            current_arm_state = t.rArm if self.arm_index == Side.RIGHT else t.lArm
            updated_arm_state = world.convert_ref_frame(
                new_arm_state, current_arm_state.refFrame,
                current_arm_state.refFrameLandmark)
            # Note that this will be overwritten later when computing IK
            updated_arm_state.joint_pose = current_arm_state.joint_pose[:]
            if self.arm_index == Side.RIGHT:
                t.rArm = updated_arm_state
            else:
                t.lArm = updated_arm_state
            self.update_viz()
        elif self.action_step.type == ActionStep.ARM_TRAJECTORY:
            rospy.logwarn('Modification of whole trajectory segments is not ' +
                          'implemented.')

    def get_absolute_position(self, is_start=True):
        """Returns the absolute position of the action step.

        Args:
            is_start (bool, optional). For trajectories only. Whether to
                get the final position in the trajectory. Defaults to
                True.

        Returns:
            Point
        """
        return self.get_absolute_pose(is_start).position

    def get_absolute_pose(self, is_start=True):
        """Returns the absolute pose of the action step.

        Args:
            is_start (bool, optional). For trajectories only. Whether to
                get the final pose in the trajectory. Defaults to True.

        Returns:
            Pose
        """
        if self.action_step.type == ActionStep.ARM_TARGET:
            # "Normal" saved pose.
            t = self.action_step.armTarget
            arm_state = t.rArm if self.arm_index == Side.RIGHT else t.lArm
        elif self.action_step.type == ActionStep.ARM_TRAJECTORY:
            # Trajectory.
            t = self.action_step.armTrajectory
            arm = t.rArm if self.arm_index == Side.RIGHT else t.lArm
            # TODO(mbforbes): Make sure this isn't a bug in the original
            # implementation. Wouldn't is_start imply you want the first one?
            index = len(arm) - 1 if is_start else 0
            arm_state = arm[index]

        # TODO(mbforbes): Figure out if there are cases that require
        # this, or remove.
        # if (arm_pose.refFrame == ArmState.OBJECT and
        #     self._world.has_object(arm_pose.refFrameLandmark.name)):
        #     return ActionStepMarker._offset_pose(arm_pose.ee_pose)
        # else:
        world_pose = world.get_absolute_pose(arm_state)
        return world_pose

    def get_pose(self):
        """Returns the pose of the action step.

        Returns:
            Pose
        """
        target = self.get_target()
        if target is not None:
            return target.ee_pose

    def set_target(self, target):
        """Sets the new ArmState for this action step.

        Args:
            target (ArmState): Replacement for this target.
        """
        if self.action_step.type == ActionStep.ARM_TARGET:
            at = self.action_step.armTarget
            if self.arm_index == Side.RIGHT:
                at.rArm = target
            else:
                at.lArm = target
            self.has_object = True
            self._update_menu()
        self.is_edited = False

    def get_target(self, traj_index=None):
        """Returns the ArmState for this action step.

        Args:
            traj_index (int, optional): Which step in the trajectory to
                return the ArmState for. Only applicable for
                trajectories (not "normal" saved poses). Defaults to
                None, in which case the middle point is used.

        Returns:
            ArmState
        """
        if self.action_step.type == ActionStep.ARM_TARGET:
            t = self.action_step.armTarget
            return t.rArm if self.arm_index == Side.RIGHT else t.lArm
        elif self.action_step.type == ActionStep.ARM_TRAJECTORY:
            t = self.action_step.armTrajectory
            arm = t.rArm if self.arm_index == Side.RIGHT else t.lArm
            # If traj_index not passed, use the middle one.
            if traj_index is None:
                traj_index = int(len(arm) / 2)
            return arm[traj_index]

    def update_viz(self):
        """Updates visualization fully."""
        self._update_viz_core()
        self._menu_handler.reApply(ActionStepMarker._im_server)
        ActionStepMarker._im_server.applyChanges()

    def pose_reached(self):
        """Update when a requested pose is reached."""
        self.is_requested = False

    def subent_cb(self, feedback):
        """Callback for when a reference frame obj/surface center change is requested.

        Args:
            feedback (InteractiveMarkerFeedback (?))
        """
        if "Obj" in self._get_ref_name():
            menu_id = self._menu_options[-1]
        else:
            menu_id = self._get_menu_id(self._get_ref_name())
        self._menu_handler.setCheckState(
                menu_id, MenuHandler.UNCHECKED)
        self._menu_handler.setCheckState(
                feedback.menu_entry_id, MenuHandler.CHECKED)
        old_ref = self._get_ref_name()
        new_ref = self._get_menu_name(feedback.menu_entry_id)
        self._set_ref(new_ref)
        rospy.loginfo(
            'Switching reference frame from {} to {} for action step {}'.format(
                old_ref, new_ref, self._get_name()))
        self._menu_handler.reApply(ActionStepMarker._im_server)
        ActionStepMarker._im_server.applyChanges()
        self.update_viz()

    def select_one_cb(self, feedback):
        """Callback for when a reference frame change to an object side is requested.
    
        Args:
            feedback (InteractiveMarkerFeedback (?))
        """
        World.selected_obj_side = None
        state = self._menu_handler.getCheckState(feedback.menu_entry_id)
        self._menu_handler.setCheckState(self._get_menu_id(self._get_ref_name()), MenuHandler.UNCHECKED)
        self._menu_handler.setCheckState(feedback.menu_entry_id, MenuHandler.CHECKED)
        rospy.loginfo('Left-click on a marker side to use it as a reference frame')
        ref_marker = World.wait_for_selection()

        for obj in World.get_frame_list(self._world):
            if obj.name == World.selected_obj_side.marker_name:
                obj_num = World.get_frame_list(self._world).index(obj)

        if "X-Minimum" in World.selected_obj_side.control_name:
            self._set_ref('Obj #' + str(obj_num) + ' X-Minimum Ref')
        elif "X-Maximum" in World.selected_obj_side.control_name:
            self._set_ref('Obj #' + str(obj_num) + ' X-Maximum Ref')
        elif "Y-Minimum" in World.selected_obj_side.control_name:
            self._set_ref('Obj #' + str(obj_num) + ' Y-Minimum Ref')
        elif "Y-Maximum" in World.selected_obj_side.control_name:
            self._set_ref('Obj #' + str(obj_num) + ' Y-Maximum Ref')
        elif "Z-Minimum" in World.selected_obj_side.control_name:
            self._set_ref('Obj #' + str(obj_num) + ' Z-Minimum Ref')
        elif "Z-Maximum" in World.selected_obj_side.control_name:
            self._set_ref('Obj #' + str(obj_num) + ' Z-Maximum Ref')
        else:
            rospy.loginfo("\nSelected object not valid to reference")

        self._menu_handler.reApply(ActionStepMarker._im_server)
        ActionStepMarker._im_server.applyChanges()
        self.update_viz()

    def marker_feedback_cb(self, feedback):
        """Callback for when an event occurs on the marker.

        Args:
            feedback (InteractiveMarkerFeedback)
        """
        if feedback.event_type == InteractiveMarkerFeedback.POSE_UPDATE:
            # The interactive marker is shifted forward from the actual
            # end-effector pose, so when setting the new pose, we need to shift
            # back from where the marker is.
            feedback_arm_state = ArmState()
            feedback_arm_state.ee_pose = ActionStepMarker._offset_pose(
                feedback.pose, -1)
            feedback_arm_state.refFrame = ArmState.ROBOT_BASE
            self.update_pose(feedback_arm_state)
            self.update_viz()
        elif feedback.event_type == InteractiveMarkerFeedback.BUTTON_CLICK:
            # Set the visibility of the 6DOF controller.
            # This happens a ton, and doesn't need to be logged like
            # normal events (e.g. clicking on most marker controls
            # fires here).
            rospy.logdebug('Changing visibility of the pose controls.')
            self.is_control_visible = not self.is_control_visible
            ActionStepMarker._marker_click_cb(self.get_uid(),
                                              self.is_control_visible)
        else:
            # This happens a ton, and doesn't need to be logged like
            # normal events (e.g. clicking on most marker controls
            # fires here).
            rospy.logdebug('Unknown event: ' + str(feedback.event_type))

    # TODO(mbforbes): Figure out types of objects sent to these
    # callbacks.

    def delete_step_cb(self, __):
        """Callback for when delete is requested.

        Args:
            __ (???): Unused
        """
        self.is_deleted = True

    def move_to_cb(self, __):
        """Callback for when moving to a pose is requested.

        Args:
            __ (???): Unused
        """
        self.is_requested = True

    def move_pose_to_cb(self, __):
        """Callback for when a pose change to current is requested.

        Args:
            __ (???): Unused

        """
        self.is_edited = True

    # ##################################################################
    # Instance methods: Internal ("private")
    # ##################################################################

    def _is_reachable(self):
        """Checks and returns whether there is an IK solution for this
        action step.

        Returns:
            bool: Whether this action step is reachable.
        """
        dummy, is_reachable = Arms.solve_ik_for_arm(self.arm_index,
                                                    self.get_target())
        # A bit more complicated logging to avoid spamming the logs
        # while still giving useful info. It now logs when reachability
        # is first calculated, or changes.
        report = False

        # See if we've set reachability for the first time.
        if self._prev_is_reachable is None:
            report = True
            reachable_str = ('is reachable' if is_reachable else
                             'is not reachable')
        # See if we've got a different reachability than we had before.
        elif self._prev_is_reachable != is_reachable:
            report = True
            reachable_str = ('is now reachable' if is_reachable else
                             'is no longer reachable')

        # Log if it's changed.
        if report:
            rospy.loginfo('Pose (' + str(self.step_number) + ', ' +
                          self.arm_name + ') ' + reachable_str)

        # Cache and return.
        self._prev_is_reachable = is_reachable
        return is_reachable

    def _get_name(self):
        """Generates the unique name for the marker.

        Returns:
            str: A human-readable unique name for the marker.
        """
        return 'step' + str(self.step_number) + 'arm' + str(self.arm_index)

    def _update_menu(self):
        """Recreates the menu when something has changed."""
        self._menu_handler = MenuHandler()

        # Insert sub entries.
        self._sub_entries = []
        self._menu_options = []
        frame_entry = self._menu_handler.insert(MENU_OPTIONS['ref'])
        refs = ActionStepMarker._ref_names
        for ref in refs:
            if ref != BASE_LINK and "Obj" not in str(ref):
                subent = self._menu_handler.insert(
                    ref + ' center', parent=frame_entry, callback=self.subent_cb)
                self._sub_entries += [subent]
                self._menu_options += [subent]
            elif ref == BASE_LINK:
                subent = self._menu_handler.insert(
                        ref, parent=frame_entry, callback=self.subent_cb)
                self._sub_entries += [subent]
                self._menu_options += [subent]
        select_one = self._menu_handler.insert(
            'Select an object side', parent=frame_entry, callback=self.select_one_cb)
        self._menu_options += [select_one]

        # Inset main menu entries.
        self._menu_handler.insert(MENU_OPTIONS['move_here'],
                                  callback=self.move_to_cb)
        self._menu_handler.insert(MENU_OPTIONS['move_current'],
                                  callback=self.move_pose_to_cb)
        self._menu_handler.insert(MENU_OPTIONS['del'],
                                  callback=self.delete_step_cb)

        # Make all unchecked to start.
        for subent in self._sub_entries:
            self._menu_handler.setCheckState(subent, MenuHandler.UNCHECKED)
        self._menu_handler.setCheckState(select_one, MenuHandler.UNCHECKED)

        # Check if necessary. Set base_link to checked
        menu_id = self._get_menu_id(self._get_ref_name())
        if menu_id is None:
            self.has_object = False
        else:
            self._menu_handler.setCheckState(menu_id, MenuHandler.CHECKED)

        # Update.
        self._update_viz_core()
        self._menu_handler.apply(ActionStepMarker._im_server, self._get_name())
        ActionStepMarker._im_server.applyChanges()

    def _get_menu_id(self, ref_name):
        """Returns the unique menu id from its name or None if the
        object is not found.

        Returns:
            int (?)|None
        """
        if ref_name in ActionStepMarker._ref_names:
            index = ActionStepMarker._ref_names.index(ref_name)
            if "Obj" in ref_name:
                return (self._menu_options[-1])
            else:
                return self._menu_options[index]
        else:
            return None

    def _get_menu_name(self, menu_id):
        """Returns the menu name from its unique menu id.

        Returns:
            str
        """
        index = self._menu_options.index(menu_id)
        return ActionStepMarker._ref_names[index]

    def _get_ref_name(self):
        """Returns the name string for the reference frame object of the
        action step.

        Returns:
            str|None: Under all normal circumstances, returns the str
                reference frame name. Returns None in error.
        """
        if self.action_step.type == ActionStep.ARM_TARGET:
            # "Normal" step (saved pose).
            arm_target = self.action_step.armTarget
            arm_state = arm_target.rArm if self.arm_index == Side.RIGHT else arm_target.lArm
            if arm_state.refFrame == ArmState.ROBOT_BASE:
                return BASE_LINK
            elif arm_state.refFrame == ArmState.OBJECT:
                return arm_state.refFrameLandmark.name
            else:
                return None
        elif self.action_step.type == ActionStep.ARM_TRAJECTORY:
            # "Trajectory" step.
            t = self.action_step.armTrajectory
            if self.arm_index == Side.RIGHT:
                return t.rRefFrameLandmark.name
            else:
                return t.lRefFrameLandmark.name
        else:
            rospy.logerr(
                'Unhandled marker type: ' + str(self.action_step.type))
            return None

    def _set_ref(self, new_ref_name):
        """Changes the reference frame of the action step to
        new_ref_name.

        Args:
            new_ref_name
        """
        # Get the id of the new ref (an int).
        new_ref = world.get_ref_from_name(new_ref_name)
        if new_ref != ArmState.ROBOT_BASE:
            index = ActionStepMarker._ref_names.index(new_ref_name)
            new_ref_obj = ActionStepMarker._ref_object_list[index - 1]
        else:
            new_ref_obj = Landmark()

        if self.action_step.type == ActionStep.ARM_TARGET:
            # Handle "normal" steps (saved poses).
            t = self.action_step.armTarget
            if self.arm_index == Side.RIGHT:
                t.rArm = world.convert_ref_frame(t.rArm, new_ref, new_ref_obj)
            else:
                t.lArm = world.convert_ref_frame(t.lArm, new_ref, new_ref_obj)
        elif self.action_step.type == ActionStep.ARM_TRAJECTORY:
            # Handle trajectory steps.
            t = self.action_step.armTrajectory
            arm = t.rArm if self.arm_index == Side.RIGHT else t.lArm
            for i in range(len(arm)):
                arm_old = arm[i]
                arm_new = world.convert_ref_frame(arm_old, new_ref,
                                                  new_ref_obj)
                arm[i] = arm_new
            # Fix up reference frames.
            if self.arm_index == Side.RIGHT:
                t.rRefFrameLandmark = new_ref_obj
                t.rRefFrame = new_ref
            else:
                t.lRefFrameLandmark = new_ref_obj
                t.lRefFrame = new_ref

    def _is_hand_open(self):
        """Returns whether the gripper is open for this action step.

        Returns:
            bool
        """
        ga = self.action_step.gripperAction
        gstate = ga.rGripper if self.arm_index == Side.RIGHT else ga.lGripper
        return gstate.state == GripperState.OPEN

    def _get_traj_pose(self, index):
        """Returns this trajectory's pose at index. Only applicable for
        trajectories.

        Args:
            index (int): Which step in the trajectory to return the
                pose from.

        Returns:
            Pose
        """
        if self.action_step.type == ActionStep.ARM_TRAJECTORY:
            at = self.action_step.armTrajectory
            arm_states = at.rArm if self.arm_index == Side.RIGHT else at.lArm
            return arm_states[index].ee_pose
        else:
            rospy.logerr(
                'Cannot request trajectory pose on non-trajectory action ' +
                'step.')

    def _update_viz_core(self):
        """Updates visualization after a change."""
        # Create a new IM control.
        menu_control = InteractiveMarkerControl()
        menu_control.interaction_mode = InteractiveMarkerControl.BUTTON
        menu_control.always_visible = True

        # Multiplex marker types added based on action step type.
        if self.action_step.type == ActionStep.ARM_TARGET:
            # Handle "normal" steps (saved poses).
            menu_control = self._make_gripper_marker(menu_control,
                                                     self._is_hand_open())
        elif self.action_step.type == ActionStep.ARM_TRAJECTORY:
            # Handle trajectories.
            # First, get all trajectory positions.
            point_list = []
            for j in range(len(self.action_step.armTrajectory.timing)):
                point_list.append(self._get_traj_pose(j).position)

            # Add a main maker for all points in the trajectory (sphere
            # list).
            # NOTE(jstn): This will not work anymore, trajectories are
            # unsupported for now.
            menu_control.markers.append(
                Marker(type=Marker.SPHERE_LIST,
                       id=self.get_uid(),
                       lifetime=TRAJ_MARKER_LIFETIME,
                       scale=SCALE_TRAJ_STEP_SPHERES,
                       header=Header(frame_id=frame_id),
                       color=COLOR_TRAJ_STEP_SPHERES,
                       points=point_list))

            # Add a marker for the first step in the trajectory.
            menu_control.markers.append(ActionStepMarker._make_sphere_marker(
                self.get_uid() + ID_OFFSET_TRAJ_FIRST, self._get_traj_pose(0),
                frame_id, TRAJ_ENDPOINT_SCALE))

            # Add a marker for the last step in the trajectory.
            last_index = len(self.action_step.armTrajectory.timing) - 1
            menu_control.markers.append(ActionStepMarker._make_sphere_marker(
                self.get_uid() + ID_OFFSET_TRAJ_LAST,
                self._get_traj_pose(last_index), frame_id,
                TRAJ_ENDPOINT_SCALE))
        else:
            # Neither "normal" pose nor trajectory; error...
            rospy.logerr(
                'Non-handled action step type ' + str(self.action_step.type))

        # Add an arrow to the relative object, if there is one.
        base_pose = world.get_absolute_pose(self.get_target())
        # base_pose refers to the wrist link, which looks weird in
        # visualizations, so offset_pose is shifted forward to be in the
        # "middle" of the gripper
        offset_pose = ActionStepMarker._offset_pose(base_pose, 1)
        ref_frame = world.get_ref_from_name(self._get_ref_name())
        if ref_frame == ArmState.OBJECT:
            menu_control.markers.append(
                Marker(type=Marker.ARROW,
                       id=(ID_OFFSET_REF_ARROW + self.get_uid()),
                       scale=SCALE_OBJ_REF_ARROW,
                       header=Header(frame_id=BASE_LINK),
                       color=COLOR_OBJ_REF_ARROW,
                       points=[offset_pose.position, self.get_target(
                       ).refFrameLandmark.pose.position]))

        # Make and add the text for this step ('Step X').
        text_pos = Point()
        text_pos.x = offset_pose.position.x
        text_pos.y = offset_pose.position.y
        text_pos.z = offset_pose.position.z + TEXT_Z_OFFSET
        menu_control.markers.append(
            Marker(type=Marker.TEXT_VIEW_FACING,
                   id=self.get_uid(),
                   scale=SCALE_STEP_TEXT,
                   text='Step ' + str(self.step_number),
                   color=COLOR_STEP_TEXT,
                   header=Header(frame_id=BASE_LINK),
                   pose=Pose(text_pos, Quaternion(0, 0, 0, 1))))

        # Make and add interactive marker.
        int_marker = InteractiveMarker()
        int_marker.name = self._get_name()
        int_marker.header.frame_id = BASE_LINK
        int_marker.pose = offset_pose
        int_marker.scale = INT_MARKER_SCALE
        self._add_6dof_marker(int_marker, True)
        int_marker.controls.append(menu_control)
        ActionStepMarker._im_server.insert(int_marker, self.marker_feedback_cb)

    def _add_6dof_marker(self, int_marker, is_fixed):
        """Adds a 6 DoF control marker to the interactive marker.

        Args:
            int_marker (InteractiveMarker)
            is_fixed (bool): Looks like whether position is fixed (?).
                Currently only passed as True.
        """
        # Each entry in options is (name, orientation, is_move)
        options = [('rotate_x', Quaternion(1, 0, 0, 1), False),
                   ('move_x', Quaternion(1, 0, 0, 1), True),
                   ('rotate_z', Quaternion(0, 1, 0, 1), False),
                   ('move_z', Quaternion(0, 1, 0, 1), True),
                   ('rotate_y', Quaternion(0, 0, 1, 1), False),
                   ('move_y', Quaternion(0, 0, 1, 1), True), ]
        for opt in options:
            name, orient, is_move = opt
            control = self._make_6dof_control(name, orient, is_move, is_fixed)
            int_marker.controls.append(control)

    def _make_6dof_control(self, name, orientation, is_move, is_fixed):
        """Creates and returns one component of the 6dof controller.

        Args:
            name (str): Name for hte control
            orientation (Quaternion): How the control should be
                oriented.
            is_move (bool): Looks like whether the marker moves the
                object (?). Currently passed as True for moving markers,
                False for rotating ones.
            is_fixed (bool): Looks like whether position is fixed (?).
                Currently always passed as True.

        Returns:
            InteractiveMarkerControl
        """
        control = InteractiveMarkerControl()
        control.name = name
        control.orientation = orientation
        control.always_visible = False
        if self.is_control_visible:
            if is_move:
                control.interaction_mode = InteractiveMarkerControl.MOVE_AXIS
            else:
                control.interaction_mode = InteractiveMarkerControl.ROTATE_AXIS
        else:
            control.interaction_mode = InteractiveMarkerControl.NONE
        if is_fixed:
            control.orientation_mode = InteractiveMarkerControl.FIXED
        return control

    def _make_mesh_marker(self):
        """Creates and returns a mesh marker.

        Returns:
            Marker
        """
        mesh = Marker()
        mesh.mesh_use_embedded_materials = False
        mesh.type = Marker.MESH_RESOURCE
        mesh.scale.x = 1.0
        mesh.scale.y = 1.0
        mesh.scale.z = 1.0
        mesh.color = self._get_mesh_marker_color()
        return mesh

    def _get_mesh_marker_color(self):
        """Gets the color for the mesh marker (thing that looks like a
        gripper) for this step.

        A simple implementation of this will return one color for
        reachable poses, another for unreachable ones. Future
        implementations may provide further visual cues.

        Returns:
            ColorRGBA: The color for the gripper mesh for this step.
        """
        if self._is_reachable():
            return COLOR_MESH_REACHABLE
        else:
            return COLOR_MESH_UNREACHABLE

    def _make_gripper_marker(self, control, is_hand_open=False):
        """Makes a gripper marker, adds it to control, returns control.

        Args:
            control (InteractiveMarkerControl): IM Control we're using.
            is_hand_open (bool, optional): Whether the gripper is open.
                Defaults to False (closed).

        Returns:
            InteractiveMarkerControl: The passed control.
        """
        # Set angle of meshes based on gripper open vs closed.
        angle = ANGLE_GRIPPER_OPEN if is_hand_open else ANGLE_GRIPPER_CLOSED

        # Make transforms in preparation for meshes 1, 2, and 3.
        # NOTE(mbforbes): There are some magic numbers in here that are
        # used a couple times. Seems like a good candidate for
        # refactoring to constants, but I think they're more clear if
        # left in here as (a) they likely won't be changed, and (b) it's
        # easier to understand the computations with them here.
        transform1 = tf.transformations.euler_matrix(0, 0, angle)
        transform1[:3, 3] = [0.07691 - ActionStepMarker._offset, 0.01, 0]
        transform2 = tf.transformations.euler_matrix(0, 0, -angle)
        transform2[:3, 3] = [0.09137, 0.00495, 0]
        t_proximal = transform1
        t_distal = tf.transformations.concatenate_matrices(transform1,
                                                           transform2)

        # Create mesh 1 (palm).
        mesh1 = self._make_mesh_marker()
        mesh1.mesh_resource = STR_GRIPPER_PALM_FILE
        mesh1.pose.position.x = -ActionStepMarker._offset
        mesh1.pose.orientation.w = 1

        # Create mesh 2 (finger).
        mesh2 = self._make_mesh_marker()
        mesh2.mesh_resource = STR_GRIPPER_FINGER_FILE
        mesh2.pose = world.get_pose_from_transform(t_proximal)

        # Create mesh 3 (fingertip).
        mesh3 = self._make_mesh_marker()
        mesh3.mesh_resource = STR_GRIPPER_FINGERTIP_FILE
        mesh3.pose = world.get_pose_from_transform(t_distal)

        # Make transforms in preparation for meshes 4 and 5.
        quat = tf.transformations.quaternion_multiply(
            tf.transformations.quaternion_from_euler(numpy.pi, 0, 0),
            tf.transformations.quaternion_from_euler(0, 0, angle))
        transform1 = tf.transformations.quaternion_matrix(quat)
        transform1[:3, 3] = [0.07691 - ActionStepMarker._offset, -0.01, 0]
        transform2 = tf.transformations.euler_matrix(0, 0, -angle)
        transform2[:3, 3] = [0.09137, 0.00495, 0]
        t_proximal = transform1
        t_distal = tf.transformations.concatenate_matrices(transform1,
                                                           transform2)

        # Create mesh 4 (other finger).
        mesh4 = self._make_mesh_marker()
        mesh4.mesh_resource = STR_GRIPPER_FINGER_FILE
        mesh4.pose = world.get_pose_from_transform(t_proximal)

        # Create mesh 5 (other fingertip).
        mesh5 = self._make_mesh_marker()
        mesh5.mesh_resource = STR_GRIPPER_FINGERTIP_FILE
        mesh5.pose = world.get_pose_from_transform(t_distal)

        # Append all meshes we made.
        control.markers.append(mesh1)
        control.markers.append(mesh2)
        control.markers.append(mesh3)
        control.markers.append(mesh4)
        control.markers.append(mesh5)

        # Return back the control.
        # TODO(mbforbes): Why do we do this?
        return control
