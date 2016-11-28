# -*- coding: utf-8 -*-

"""
This module contains the Qudi Hardware module attocube ANC300 .

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import abc
import telnetlib

from core.base import Base
from interface.confocal_scanner_interface import ConfocalScannerInterface

host = "134.60.31.214"
password = b"123456"
port = "7230"
_mode_list = ["gnd", "inp", "cap", "stp", "off", "stp+", "stp-"]

class AttoCubeStepper(Base, ConfocalScannerInterface):
    """ This is the Interface class to define the controls for the simple
    microwave hardware.
    """

    def on_activate(self, e):
        """ Initialisation performed during activation of the module.

        @param object e: Event class object from Fysom.
                         An object created by the state machine module Fysom,
                         which is connected to a specific event (have a look in
                         the Base Class). This object contains the passed event,
                         the state before the event happened and the destination
                         of the state which should be reached after the event
                         had happened.
        """
        config = self.getConfiguration()

        # connect ethernet socket and FTP
        self.tn = telnetlib.Telnet(host, port)
        self.tn.open(host, port)
        self.tn.read_until(b"Authorization code: ")
        self.tn.write(password+ b"\n")
        value = self.tn.read_until(b'success')
        self.connected = True

        if 'attocube_axis' in config.keys():
            self._attocube_axis = config['attocube_axis']
        else:
            self.log.error(
                'No parameter "attocube_axis" found in configuration!\n'
                'Assign to that parameter an appropriated channel sorting!')

    def on_deactivate(self, e):
        """ Deinitialisation performed during deactivation of the module.

        @param object e: Event class object from Fysom. A more detailed
                         explanation can be found in method activation.
        """
        self.tn.close()
        self.connected = False

    _modtype = 'AttoCubeStepper'
    _modclass = 'hardware'

    # =================== Attocube Communication ========================

    def send_cmd(self, cmd, expected_response=b"\r\nOK\r\n"):
        """Sends a command to the attocube steppers and checks repsonse value

        @param str cmd: Attocube ANC300 command
        @param str expected_response: expected attocube response to command

        @return int: error code (0: OK, -1:error)
        """
        full_cmd = cmd.encode('ascii')+b"\r\n" #converting to binary
        junk = self.tn.read_eager() #diregard old print outs
        self.tn.write(full_cmd) #send command
        #self.tn.read_until(full_cmd + b" = ") #read answer
        value = self.tn.read_eager()
        #TODO: here needs to be an error check, if not working, return 1, if -1 return attocube response
        return 0

    def step_attocube(self ):
        pass

    def change_attocube_mode(self, axis, mode):
        """Changes Attocube axis mode

        @param str axis: axis to be changed, can only be part of dictionary axes
        @param str mode: mode to be set
        @return int: error code (0: OK, -1:error)
        """
        if mode in _mode_list:
            if axis in ["x", "y", "z"]:
                command = "setm "+ self._attocube_axis[axis]+ mode
                return self.send_cmd(command)
            else:
                self.log.error("axis {} not in list of possible axes". format(self._attocube_axis))
                return -1
        else:
            self.log.error("mode {} not in list of possible modes". format(mode))
            return -1

    def move_attocube(self, axis, mode, direction, steps=1):
        """Moves attocubes either continuously or by a number of steps in the up or down direction.

        @param str axis: axis to be moved, can only be part of dictionary axes
        @param str mode: continuous or stepping mode
        @param str direction: "up" or "down" for z, "out" or "in" for in plane movement
        @param int steps: number of steps to be moved, ignore for continous mode
        @return int:  error code (0: OK, -1:error)
        """
        if mode in ["continuous", "stepping"]:
            if axis in ["x", "y", "z"]:
                if direction=="up" or direction=="out":
                    command = "stepu "+self._attocube_axis[axis]
                else:
                    command = "stepd "+self._attocube_axis[axis]

                if mode == "continuous":
                    command = command + "c"
                else:
                    command = command + str(steps)
                return self.send_cmd(command)
            else:
                self.log.error("axis {} not in list of possible axes".format(self._attocube_axis))
                return -1
        else:
            self.log.error("mode {} not in list of possible modes".format(mode))
            return -1

    def stop_attocube_movement(self, axis):
        """Stops attocube motion on specified axis, only necessary if attocubes are stepping in continuous mode

        @param str axis: axis to be moved, can only be part of dictionary axes
        @return int: error code (0: OK, -1:error)
        """
        if axis in ["x", "y", "z"]:
            command = "stop"+ self._attocube_axis[axis]
            return self.send_cmd(command)
        else:
            self.log.error("axis {} not in list of possible axes".format(self._attocube_axis))
            return -1

    def stop_all_attocube_motion(self):
        """Stops any attocube motion

        @return 0
        """
        self.send_cmd("stop 1")
        self.send_cmd("stop 2")
        self.send_cmd("stop 3")
        self.send_cmd("stop 4")
        self.send_cmd("stop 5")
        #There are at maximum 5 stepper axis per ANC300 module. If existing any motion on the axis is stopped
        self.log.info("any attocube stepper motion has been stopped")
        return 0

    # =================== ConfocalScannerInterface Commands ========================
    @abc.abstractmethod
    def reset_hardware(self):
        """ Resets the hardware, so the connection is lost and other programs
            can access it.

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def get_position_range(self):
        """ Returns the physical range of the scanner.

        @return float [4][2]: array of 4 ranges with an array containing lower
                              and upper limit
        """
        pass

    @abc.abstractmethod
    def set_position_range(self, myrange=None):
        """ Sets the physical range of the scanner.

        @param float [4][2] myrange: array of 4 ranges with an array containing
                                     lower and upper limit

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def set_voltage_range(self, myrange=None):
        """ Sets the voltage range of the NI Card.

        @param float [2] myrange: array containing lower and upper limit

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def set_up_scanner_clock(self, clock_frequency=None, clock_channel=None):
        """ Configures the hardware clock of the NiDAQ card to give the timing.

        @param float clock_frequency: if defined, this sets the frequency of the
                                      clock
        @param str clock_channel: if defined, this is the physical channel of
                                  the clock

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def set_up_scanner(self, counter_channel=None, photon_source=None,
                       clock_channel=None, scanner_ao_channels=None):
        """ Configures the actual scanner with a given clock.

        @param str counter_channel: if defined, this is the physical channel
                                    of the counter
        @param str photon_source: if defined, this is the physical channel where
                                  the photons are to count from
        @param str clock_channel: if defined, this specifies the clock for the
                                  counter
        @param str scanner_ao_channels: if defined, this specifies the analoque
                                        output channels

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def scanner_set_position(self, x=None, y=None, z=None, a=None):
        """Move stage to x, y, z, a (where a is the fourth voltage channel).

        @param float x: postion in x-direction (volts)
        @param float y: postion in y-direction (volts)
        @param float z: postion in z-direction (volts)
        @param float a: postion in a-direction (volts)

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def get_scanner_position(self):
        """ Get the current position of the scanner hardware.

        @return float[]: current position in (x, y, z, a).
        """
        pass

    @abc.abstractmethod
    def set_up_line(self, length=100):
        """ Sets up the analoque output for scanning a line.

        @param int length: length of the line in pixel

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def scan_line(self, line_path=None):
        """ Scans a line and returns the counts on that line.

        @param float[][4] line_path: array of 4-part tuples defining the
                                     positions pixels

        @return float[]: the photon counts per second
        """
        pass

    @abc.abstractmethod
    def close_scanner(self):
        """ Closes the scanner and cleans up afterwards.

        @return int: error code (0:OK, -1:error)
        """
        pass

    @abc.abstractmethod
    def close_scanner_clock(self, power=0):
        """ Closes the clock and cleans up afterwards.

        @return int: error code (0:OK, -1:error)
        """
        pass

