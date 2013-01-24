# Manifests
import roslib
roslib.load_manifest('rospy')
roslib.load_manifest('geometry_msgs')
roslib.load_manifest('pr2_pbd_interaction')
roslib.load_manifest('visualization_msgs')

# Generic libraries
import time, sys, signal, threading
import numpy
from numpy import *
from numpy.linalg import norm
import os
from geometry_msgs.msg import *
from visualization_msgs.msg import MarkerArray, Marker

# ROS Libraries
import rospy
import rosbag
from pr2_pbd_interaction.msg import *
from ActionStepMarker import *
from std_msgs.msg import Header,ColorRGBA

class ProgrammedAction:
    
    def __init__(self, skillIndex):
        self.seq = ActionStepSequence()
        self.skillIndex = skillIndex
        self.rMarkers = []
        self.lMarkers = []
        self.rLinks = dict()
        self.lLinks = dict()
        self.markerOutput = rospy.Publisher('visualization_marker_array', MarkerArray)
        self.lock = threading.Lock()
    
    def copy(self):
        pAction = ProgrammedAction(self.skillIndex)
        pAction.seq = ActionStepSequence()
        for i in range(len(self.seq.seq)):
            aStep = self.seq.seq[i]
            aStepCopy = self.copyActionStep(aStep)
            pAction.seq.seq.append(aStepCopy)
        return pAction

    def copyActionStep(self, aStep):
        aStepCopy = ActionStep()
        aStepCopy.type = int(aStep.type)
        if (aStepCopy.type == ActionStep.ARM_TARGET):
            aStepCopy.armTarget = ArmTarget()
            aStepCopy.armTarget.rArmVelocity = float(aStep.armTarget.rArmVelocity)
            aStepCopy.armTarget.lArmVelocity = float(aStep.armTarget.lArmVelocity)
            aStepCopy.armTarget.rArm = self.copyArmState(aStep.armTarget.rArm)
            aStepCopy.armTarget.lArm = self.copyArmState(aStep.armTarget.lArm)
        elif (aStepCopy.type == ActionStep.ARM_TRAJECTORY):
            aStepCopy.armTrajectory = ArmTrajectory()
            aStepCopy.armTrajectory.timing = aStep.armTrajectory.timing[:]
            for j in range(len(aStep.armTrajectory.timing)):
                aStepCopy.armTrajectory.rArm.append(self.copyArmState(aStep.armTrajectory.rArm[j]))
                aStepCopy.armTrajectory.lArm.append(self.copyArmState(aStep.armTrajectory.lArm[j]))
            aStepCopy.armTrajectory.rRefFrame = int(aStep.armTrajectory.rRefFrame)
            aStepCopy.armTrajectory.lRefFrame = int(aStep.armTrajectory.lRefFrame)
            aStepCopy.armTrajectory.rRefFrameName = str(aStep.armTrajectory.rRefFrameName)
            aStepCopy.armTrajectory.lRefFrameName = str(aStep.armTrajectory.lRefFrameName)
        aStepCopy.gripperAction = GripperAction(aStep.gripperAction.rGripper, aStep.gripperAction.lGripper)
        return aStepCopy
    
    def copyArmState(self, armState):
        armStateCopy = ArmState()
        armStateCopy.refFrame = int(armState.refFrame)
        armStateCopy.joint_pose = armState.joint_pose[:]
        armStateCopy.ee_pose = Pose(armState.ee_pose.position, armState.ee_pose.orientation)
        armStateCopy.refFrameName = str(armState.refFrameName)
        return armStateCopy
    
    def getName(self):
        return 'Action' + str(self.skillIndex)

    def addActionStep(self, step, objectList):
        self.lock.acquire()
        self.seq.seq.append(self.copyActionStep(step))
        
        if (step.type == ActionStep.ARM_TARGET or step.type == ActionStep.ARM_TRAJECTORY):
            self.rMarkers.append(ActionStepMarker(self.nFrames(), 0, self.getLastStep(), objectList))
            self.lMarkers.append(ActionStepMarker(self.nFrames(), 1, self.getLastStep(), objectList))
            if (self.nFrames() > 1):
                self.rLinks[self.nFrames()-1] = self.getLink(0, self.nFrames()-1)
                self.lLinks[self.nFrames()-1] = self.getLink(1, self.nFrames()-1)
        self.lock.release()
            
    def getLink(self, armIndex, toIndex):
        if (armIndex == 0):
            startPoint = self.rMarkers[toIndex-1].getAbsolutePosition(isStart=True)
            endPoint = self.rMarkers[toIndex].getAbsolutePosition(isStart=False)
        else:
            startPoint = self.lMarkers[toIndex-1].getAbsolutePosition(isStart=True)
            endPoint = self.lMarkers[toIndex].getAbsolutePosition(isStart=False)
            
        return Marker(type=Marker.ARROW, id=(2*toIndex+armIndex), lifetime=rospy.Duration(2),
                      scale=Vector3(0.01,0.03,0.01), header=Header(frame_id='base_link'),
                      color=ColorRGBA(0.8, 0.8, 0.8, 0.3), points=[startPoint, endPoint])

    def updateObjects(self, objectList):
        self.updateInteractiveMarkers()
        for i in range(len(self.rMarkers)):
            self.rMarkers[i].updateReferenceFrameList(objectList)
        for i in range(len(self.lMarkers)):
            self.lMarkers[i].updateReferenceFrameList(objectList)
        
    def updateInteractiveMarkers(self):
        for i in range(len(self.rMarkers)):
            self.rMarkers[i].updateVisualization()
        for i in range(len(self.lMarkers)):
            self.lMarkers[i].updateVisualization()

    def resetAllTargets(self, armIndex):
        if (armIndex == 0):
            for i in range(len(self.rMarkers)):
                self.rMarkers[i].poseReached()
        else:
            for i in range(len(self.lMarkers)):
                self.lMarkers[i].poseReached()

    def deletePotentialTargets(self):
        # TODO: Complete and test!
        self.lock.acquire()
        toDelete = None
        for i in range(len(self.rMarkers)):
            if (self.rMarkers[i].isDeleteRequested or
                self.lMarkers[i].isDeleteRequested):
                rospy.loginfo('Will delete step ' + str(i+1))
                self.rMarkers[i].isDeleteRequested = False
                self.lMarkers[i].isDeleteRequested = False
                toDelete = i
                break
        self.lock.release()

        if (toDelete != None):
            self.rLinks[self.rLinks.keys()[-1]].action = Marker.DELETE
            self.lLinks[self.lLinks.keys()[-1]].action = Marker.DELETE
            self.updateVisualization()
            self.rMarkers[-1].destroy()
            self.lMarkers[-1].destroy()
            for i in range(toDelete+1, self.nFrames()):
                self.rMarkers[i].decreaseID()
                self.lMarkers[i].decreaseID()
            rMarker = self.rMarkers.pop(toDelete)
            lMarker = self.lMarkers.pop(toDelete)
            aStep = self.seq.seq.pop(toDelete)
            self.rLinks.pop(self.rLinks.keys()[-1])
            self.lLinks.pop(self.lLinks.keys()[-1])
            self.updateVisualization()
            self.updateInteractiveMarkers()

    def getPotentialTargets(self, armIndex):
        if (armIndex == 0):
            for i in range(len(self.rMarkers)):
                if (self.rMarkers[i].isPoseRequested):
                    return self.rMarkers[i].getTarget()
        else:
            for i in range(len(self.lMarkers)):
                if (self.lMarkers[i].isPoseRequested):
                    return self.lMarkers[i].getTarget()
        return None

    def updateLinks(self):
        for i in self.rLinks.keys():
            self.rLinks[i] = self.getLink(0, i)
        for i in self.lLinks.keys():
            self.lLinks[i] = self.getLink(1, i)
            
    def updateVisualization(self):
        self.lock.acquire()
        mArray = MarkerArray()
        for i in self.rLinks.keys():
            mArray.markers.append(self.rLinks[i])
        for i in self.lLinks.keys():
            mArray.markers.append(self.lLinks[i])
        self.markerOutput.publish(mArray)
        self.updateLinks()
        self.lock.release()
        
    def clear(self):
        #TODO: get backups before clear
        self.seq = ActionStepSequence()
        self.rMarkers = []
        self.lMarkers = []
        self.rLinks = dict()
        self.lLinks = dict()

    def undoClear(self):
        self.seq = [] #TODO
        
    def getFname(self, ext='.bag'):
        if ext[0] != '.' :
            ext  = '.' + ext
        return self.getName() + ext
    
    def nFrames(self):
        return len(self.seq.seq)
    
    def save(self, dataDir):
        if (self.nFrames() > 0):
            for i in range(len(self.seq.seq)):
                aStep = self.seq.seq[i]
                print i, '(R):', aStep.armTarget.rArm.joint_pose 
                print i, '(L):', aStep.armTarget.lArm.joint_pose 
            demoBag = rosbag.Bag(dataDir + self.getFname(), 'w')
            demoBag.write('sequence', self.seq)
            demoBag.close()
        else:
            rospy.logwarn('Could not save demonstration because it does not have any frames.')
        
    def load(self, dataDir):
        fname = dataDir + self.getFname()
        if (os.path.exists(fname)):
            demoBag = rosbag.Bag(fname)
            for topic, msg, t in demoBag.read_messages(topics=['sequence']):
                print 'Reading demo bag file at time ', t.to_sec()
                self.seq = msg
            print self.seq
            demoBag.close()
        else:
            rospy.logwarn('File does not exist, cannot load demonstration: '+ fname)
    
    def resetVisualization(self):
        self.lock.acquire()
        for i in range(len(self.rMarkers)):
            self.rMarkers[i].destroy()
            self.lMarkers[i].destroy()
        for i in self.rLinks.keys():
            self.rLinks[i].action = Marker.DELETE
            self.lLinks[i].action = Marker.DELETE
        mArray = MarkerArray()
        for i in self.rLinks.keys():
            mArray.markers.append(self.rLinks[i])
        for i in self.lLinks.keys():
            mArray.markers.append(self.lLinks[i])
        self.markerOutput.publish(mArray)
        self.rMarkers = []
        self.lMarkers = []
        self.rLinks = dict()
        self.lLinks = dict()
        self.lock.release()
        
    def initializeVisualization(self, objectList):
        for i in range(len(self.seq.seq)):
            step = self.seq.seq[i]
            if (step.type == ActionStep.ARM_TARGET or step.type == ActionStep.ARM_TRAJECTORY):
                self.rMarkers.append(ActionStepMarker(i+1, 0, step, objectList))
                self.lMarkers.append(ActionStepMarker(i+1, 1, step, objectList))
                if (i > 0):
                    self.rLinks[i] = self.getLink(0, i)
                    self.lLinks[i] = self.getLink(1, i)
        self.updateInteractiveMarkers()

    def getLastStep(self):
        return self.seq.seq[len(self.seq.seq)-1]
    
    def deleteLastStep(self):
        # TODO: backup last pose for undo
        self.seq.seq = self.seq.seq[0:len(self.seq.seq)-1]
        
    def resumeDeletedPose(self):
        self.seq.seq.append(None) #TODO

    ## TODO??        
    def getTrajectory(self):
        return self.seq
    
    def requiresObject(self):
        for i in range(len(self.seq.seq)):
            if ((self.seq.seq[i].type == ActionStep.ARM_TARGET and 
                (self.seq.seq[i].armTarget.rArm.refFrame == ArmState.OBJECT or 
                 self.seq.seq[i].armTarget.lArm.refFrame == ArmState.OBJECT)) or 
                (self.seq.seq[i].type == ActionStep.ARM_TRAJECTORY and 
                (self.seq.seq[i].armTrajectory.rRefFrame == ArmState.OBJECT or 
                 self.seq.seq[i].armTrajectory.lRefFrame == ArmState.OBJECT))):
                return True
        return False

    def getStep(self, index):
        return self.seq.seq[index]
