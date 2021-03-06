# -*- coding: utf-8 -*
"""
This file contains the Qudi logic class for optimizing scanner position.

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
Copyright (c) 2016 Christoph Müller  cmueller2603@gmail.com
Copyright (c) 2016 Florian Frank  florian.frank@uni-ulm.de
"""

from qtpy import QtCore
import numpy as np
import time

from logic.generic_logic import GenericLogic
from core.util.mutex import Mutex


class OptimizerLogic(GenericLogic):

    """This is the Logic class for optimizing scanner position on bright features.
    """

    _modclass = 'optimizerlogic'
    _modtype = 'logic'

    # declare connectors
    _in = {'confocalscanner1': 'ConfocalScannerInterface',
           'fitlogic': 'FitLogic'
           }
    _out = {'optimizerlogic': 'OptimizerLogic'}

    # "private" signals to keep track of activities here in the optimizer logic
    _signal_scan_next_xy_line = QtCore.Signal()
    _signal_scan_z_line = QtCore.Signal()
    _signal_completed_xy_optimizer_scan = QtCore.Signal()
    _signal_do_next_optimization_step = QtCore.Signal()
    _signal_finished_all_optimization_steps = QtCore.Signal()

    # public signals
    signal_image_updated = QtCore.Signal()
    signal_refocus_started = QtCore.Signal()
    signal_refocus_XY_size_changed = QtCore.Signal()
    signal_refocus_Z_size_changed = QtCore.Signal()
    signal_refocus_finished = QtCore.Signal(str, list)

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self.log.info('The following configuration was found.')

        # checking for the right configuration
        for key in config.keys():
            self.log.info('{0}: {1}'.format(key, config[key]))

        # setting standard parameter for refocus
        self.refocus_XY_size = 0.6
        self.optimizer_XY_res = 10
        self.refocus_Z_size = 2
        self.optimizer_Z_res = 30
        self.hw_settle_time = 0.1  # let scanner reach start of xy and z scans

        # Initialization of settings option for optimization sequence
        self.optimization_sequence = ['XY', 'Z']

        # settings option for surface subtraction in depth scan
        self.do_surface_subtraction = False
        self.surface_subtr_scan_offset = 1  # micron

        # locking for thread safety
        self.threadlock = Mutex()

        self.stopRequested = False
        self.is_crosshair = True

        # Keep track of who called the refocus
        self._caller_tag = ''

    def on_activate(self, e):
        """ Initialisation performed during activation of the module.

        @param e: error code

        @return int: error code (0:OK, -1:error)
        """
        self._scanning_device = self.get_in_connector('confocalscanner1')
        self._fit_logic = self.get_in_connector('fitlogic')

        # default values for clock frequency and slowness
        # slowness: steps during retrace line
        if 'clock_frequency' in self._statusVariables:
            self._clock_frequency = self._statusVariables['clock_frequency']
        else:
            self._clock_frequency = 50
        if 'return_slowness' in self._statusVariables:
            self.return_slowness = self._statusVariables['return_slowness']
        else:
            self.return_slowness = 20

        # Reads in the maximal scanning range. The unit of that scan range is micrometer!
        self.x_range = self._scanning_device.get_position_range()[0]
        self.y_range = self._scanning_device.get_position_range()[1]
        self.z_range = self._scanning_device.get_position_range()[2]

        self._initial_pos_x = 0.
        self._initial_pos_y = 0.
        self._initial_pos_z = 0.
        self.optim_pos_x = self._initial_pos_x
        self.optim_pos_y = self._initial_pos_y
        self.optim_pos_z = self._initial_pos_z

        self._max_offset = 3.

        # Sets the current position to the center of the maximal scanning range
        self._current_x = (self.x_range[0] + self.x_range[1]) / 2
        self._current_y = (self.y_range[0] + self.y_range[1]) / 2
        self._current_z = (self.z_range[0] + self.z_range[1]) / 2
        self._current_a = 0.0

        # tilt correction stuff:
        self.tilt_correction = False

        self.tilt_reference_x = 0.5 * (self.x_range[0] + self.x_range[1])
        self.tilt_reference_y = 0.5 * (self.y_range[0] + self.y_range[1])

        self.tilt_slope_x = 0
        self.tilt_slope_y = 0

        self.point1 = np.array((0, 0, 0))
        self.point2 = np.array((0, 0, 0))
        self.point3 = np.array((0, 0, 0))

        ###########################
        # Fit Params and Settings #
        model, params = self._fit_logic.make_gaussianwithslope_model()
        self.z_params = params
        self.use_custom_params = False
        #####################################################

        # Initialization of internal counter for scanning
        self._xy_scan_line_count = 0

        # Initialization of optimization sequence step counter
        self._optimization_step = 0

        # Sets connections between signals and functions
        self._signal_scan_next_xy_line.connect(self._refocus_xy_line, QtCore.Qt.QueuedConnection)
        self._signal_scan_z_line.connect(self.do_z_optimization, QtCore.Qt.QueuedConnection)
        self._signal_completed_xy_optimizer_scan.connect(self._set_optimized_xy_from_fit, QtCore.Qt.QueuedConnection)

        self._signal_do_next_optimization_step.connect(self._do_next_optimization_step, QtCore.Qt.QueuedConnection)
        self._signal_finished_all_optimization_steps.connect(self.finish_refocus)
        self._initialize_xy_refocus_image()
        self._initialize_z_refocus_image()
        return 0

    def on_deactivate(self, e):
        """ Reverse steps of activation

        @param e: error code

        @return int: error code (0:OK, -1:error)
        """
        self._statusVariables['clock_frequency'] = self._clock_frequency
        self._statusVariables['return_slowness'] = self.return_slowness
        return 0

    def testing(self):
        pass

    def check_optimization_sequence(self):
        """ Check the sequence of scan events for the optimization.
        """

        # Check the supplied optimization sequence only contains 'XY' and 'Z'
        if len(set(self.optimization_sequence).difference({'XY', 'Z'})) > 0:
            self.log.error(
                'Requested optimization sequence contains unknown steps.'
                ' Please provide a sequence containing only \'XY\' '
                'and \'Z\' strings. The default [\'XY\', \'Z\'] will be used.')
            self.optimization_sequence = ['XY', 'Z']

    def set_clock_frequency(self, clock_frequency):
        """Sets the frequency of the clock

        @param int clock_frequency: desired frequency of the clock

        @return int: error code (0:OK, -1:error)
        """

        self._clock_frequency = int(clock_frequency)
        # checks if scanner is still running
        if self.getState() == 'locked':
            return -1
        else:
            return 0

    def set_refocus_XY_size(self,size):
        self.refocus_XY_size = size
        self.signal_refocus_XY_size_changed.emit()

    def set_refocus_Z_size(self,size):
        self.refocus_Z_size = size
        self.signal_refocus_Z_size_changed.emit()


    def start_refocus(self, initial_pos=None, caller_tag='unknown'):
        """Starts the optimization scan around initial_pos

        @param initial_pos
        """
        # checking if refocus corresponding to crosshair or corresponding
        # to initial_pos
        if isinstance(initial_pos, (np.ndarray,)) and initial_pos.size == 3:
            self._initial_pos_x, self._initial_pos_y, self._initial_pos_z = initial_pos
        elif isinstance(initial_pos, (list, tuple)) and len(initial_pos) == 3:
            self._initial_pos_x, self._initial_pos_y, self._initial_pos_z = initial_pos
        elif initial_pos is None:
            scpos = self._scanning_device.get_scanner_position()[0:3]
            self._initial_pos_x, self._initial_pos_y, self._initial_pos_z = scpos
        else:
            pass  # TODO: throw error

        # Keep track of where the start_refocus was initiated
        self._caller_tag = caller_tag

        # Set the optim_pos values to match the initial_pos values.
        # This means we can use optim_pos in subsequent steps and ensure
        # that we benefit from any completed optimization step.
        self.optim_pos_x = self._initial_pos_x
        self.optim_pos_y = self._initial_pos_y
        self.optim_pos_z = self._initial_pos_z

        self._xy_scan_line_count = 0
        self._optimization_step = 0
        self.check_optimization_sequence()

        scanner_status = self.start_scanner()
        if scanner_status < 0:
            self.signal_refocus_finished.emit(
                self._caller_tag,
                [self.optim_pos_x, self.optim_pos_y, self.optim_pos_z, 0])
            return
        self.signal_refocus_started.emit()
        self._signal_do_next_optimization_step.emit()

    def stop_refocus(self):
        """Stops refocus."""
        with self.threadlock:
            self.stopRequested = True

    def _initialize_xy_refocus_image(self):
        """Initialisation of the xy refocus image."""
        self._xy_scan_line_count = 0

        # Take optim pos as center of refocus image, to benefit from any previous
        # optimization steps that have occurred.
        x0 = self.optim_pos_x
        y0 = self.optim_pos_y

        # defining position intervals for refocus
        xmin = np.clip(x0 - 0.5 * self.refocus_XY_size, self.x_range[0], self.x_range[1])
        xmax = np.clip(x0 + 0.5 * self.refocus_XY_size, self.x_range[0], self.x_range[1])
        ymin = np.clip(y0 - 0.5 * self.refocus_XY_size, self.y_range[0], self.y_range[1])
        ymax = np.clip(y0 + 0.5 * self.refocus_XY_size, self.y_range[0], self.y_range[1])

        self._X_values = np.linspace(xmin, xmax, num=self.optimizer_XY_res)
        self._Y_values = np.linspace(ymin, ymax, num=self.optimizer_XY_res)
        self._Z_values = self.optim_pos_z * np.ones(self._X_values.shape)
        self._A_values = np.zeros(self._X_values.shape)
        self._return_X_values = np.linspace(xmax, xmin, num=self.optimizer_XY_res)
        self._return_A_values = np.zeros(self._return_X_values.shape)

        self.xy_refocus_image = np.zeros((len(self._Y_values), len(self._X_values), 4))
        self.xy_refocus_image[:, :, 0] = np.full((len(self._Y_values), len(self._X_values)), self._X_values)
        y_value_matrix = np.full((len(self._X_values), len(self._Y_values)), self._Y_values)
        self.xy_refocus_image[:, :, 1] = y_value_matrix.transpose()
        self.xy_refocus_image[:, :, 2] = self.optim_pos_z * np.ones((len(self._Y_values), len(self._X_values)))

    def _initialize_z_refocus_image(self):
        """Initialisation of the z refocus image."""
        self._xy_scan_line_count = 0

        # Take optim pos as center of refocus image, to benefit from any previous
        # optimization steps that have occurred.
        z0 = self.optim_pos_z

        zmin = np.clip(z0 - 0.5 * self.refocus_Z_size, self.z_range[0], self.z_range[1])
        zmax = np.clip(z0 + 0.5 * self.refocus_Z_size, self.z_range[0], self.z_range[1])

        self._zimage_Z_values = np.linspace(zmin, zmax, num=self.optimizer_Z_res)
        self._fit_zimage_Z_values = np.linspace(zmin, zmax, num=self.optimizer_Z_res)
        self._zimage_A_values = np.zeros(self._zimage_Z_values.shape)
        self.z_refocus_line = np.zeros(len(self._zimage_Z_values))
        self.z_fit_data = np.zeros(len(self._fit_zimage_Z_values))

    def _move_to_start_pos(self, start_pos):
        """Moves the scanner from its current position to the start position of the optimizer scan.

        @param start_pos float[]: 3-point vector giving x, y, z position to go to.
        """
        scanner_pos = self._scanning_device.get_scanner_position()

        move_to_start_line = np.vstack((
            np.linspace(scanner_pos[0], start_pos[0], self.return_slowness),
            np.linspace(scanner_pos[1], start_pos[1], self.return_slowness),
            np.linspace(scanner_pos[2], start_pos[2], self.return_slowness),
            np.linspace(0, 0, self.return_slowness)))

        counts = self._scanning_device.scan_line(move_to_start_line)
        if counts[0] == -1:
            return -1
        time.sleep(self.hw_settle_time)
        return 0

    def _refocus_xy_line(self):
        """Scanning a line of the xy optimization image.
        This method repeats itself using the _signal_scan_next_xy_line
        until the xy optimization image is complete.
        """
        # stop scanning if instructed
        if self.stopRequested:
            with self.threadlock:
                self.stopRequested = False
                self.finish_refocus()
                self.signal_image_updated.emit()
                self.signal_refocus_finished.emit(
                    self._caller_tag,
                    [self.optim_pos_x, self.optim_pos_y, self.optim_pos_z, 0])
                return

        # move to the start of the first line
        if self._xy_scan_line_count == 0:
            status = self._move_to_start_pos(
                [self.xy_refocus_image[0, 0, 0],
                 self.xy_refocus_image[0, 0, 1],
                 self.xy_refocus_image[0, 0, 2]])
            if status < 0:
                self.log.error('Error during move to starting point.')
                self.stop_refocus()
                self._signal_scan_next_xy_line.emit()
                return

        # scan a line of the xy optimization image
        line = np.vstack((
            self.xy_refocus_image[self._xy_scan_line_count, :, 0],
            self.xy_refocus_image[self._xy_scan_line_count, :, 1],
            self.xy_refocus_image[self._xy_scan_line_count, :, 2],
            self._A_values))

        line_counts = self._scanning_device.scan_line(line)
        if line_counts[0] == -1:
            self.log.error('The scan went wrong, killing the scanner.')
            self.stop_refocus()
            self._signal_scan_next_xy_line.emit()
            return

        return_line = np.vstack((
            self._return_X_values,
            self.xy_refocus_image[self._xy_scan_line_count, 0, 1] * np.ones(self._return_X_values.shape),
            self.xy_refocus_image[self._xy_scan_line_count, 0, 2] * np.ones(self._return_X_values.shape),
            self._return_A_values))

        return_line_counts = self._scanning_device.scan_line(return_line)
        if return_line_counts[0] == -1:
            self.log.error('The scan went wrong, killing the scanner.')
            self.stop_refocus()
            self._signal_scan_next_xy_line.emit()
            return

        self.xy_refocus_image[self._xy_scan_line_count, :, 3] = line_counts
        self.signal_image_updated.emit()

        self._xy_scan_line_count += 1

        if self._xy_scan_line_count < np.size(self._Y_values):
            self._signal_scan_next_xy_line.emit()
        else:
            self._signal_completed_xy_optimizer_scan.emit()

    def _set_optimized_xy_from_fit(self):
        """Fit the completed xy optimizer scan and set the optimized xy position."""
        fit_x, fit_y = np.meshgrid(self._X_values, self._Y_values)
        xy_fit_data = self.xy_refocus_image[:, :, 3].ravel()
        axes = np.empty((len(self._X_values) * len(self._Y_values), 2))
        axes = (fit_x.flatten(), fit_y.flatten())
        result_2D_gaus = self._fit_logic.make_twoDgaussian_fit(axis=axes, data=xy_fit_data)
        # print(result_2D_gaus.fit_report())

        if result_2D_gaus.success is False:
            self.log.error('error in 2D Gaussian Fit.')
            print('2D gaussian fit not successfull')
            self.optim_pos_x = self._initial_pos_x
            self.optim_pos_y = self._initial_pos_y
            # hier abbrechen
        else:
            #                @reviewer: Do we need this. With constraints not one of these cases will be possible....
            if abs(self._initial_pos_x - result_2D_gaus.best_values['x_zero']) < self._max_offset and abs(self._initial_pos_x - result_2D_gaus.best_values['x_zero']) < self._max_offset:
                if result_2D_gaus.best_values['x_zero'] >= self.x_range[0] and result_2D_gaus.best_values['x_zero'] <= self.x_range[1]:
                    if result_2D_gaus.best_values['y_zero'] >= self.y_range[0] and result_2D_gaus.best_values['y_zero'] <= self.y_range[1]:
                        self.optim_pos_x = result_2D_gaus.best_values['x_zero']
                        self.optim_pos_y = result_2D_gaus.best_values['y_zero']
            else:
                self.optim_pos_x = self._initial_pos_x
                self.optim_pos_y = self._initial_pos_y

        # emit image updated signal so crosshair can be updated from this fit
        self.signal_image_updated.emit()
        self._signal_do_next_optimization_step.emit()

    def do_z_optimization(self):
        """ Do the z axis optimization."""
        # z scaning
        self._scan_z_line()

        self.signal_image_updated.emit()

        # z-fit
        # If subtracting surface, then data can go negative and the gaussian fit offset constraints need to be adjusted
        if self.do_surface_subtraction:
            adjusted_param = {}
            adjusted_param['offset'] = {
                'value': 1e-12,
                'min': -self.z_refocus_line.max(),
                'max': self.z_refocus_line.max()
            }
            result = self._fit_logic.make_gaussianwithslope_fit(
                axis=self._zimage_Z_values,
                data=self.z_refocus_line,
                add_parameters=adjusted_param)
        else:
            if self.use_custom_params:
                result = self._fit_logic.make_gaussianwithslope_fit(
                    axis=self._zimage_Z_values,
                    data=self.z_refocus_line,
                    # Todo: It is required that the changed parameters are given as a dictionary
                    add_parameters={})
            else:
                result = self._fit_logic.make_gaussianwithslope_fit(
                    axis=self._zimage_Z_values,
                    data=self.z_refocus_line)
        self.z_params = result.params

        if result.success is False:
            self.log.error('error in 1D Gaussian Fit.')
            self.optim_pos_z = self._initial_pos_z
            # interrupt here?
        else:  # move to new position
            #                @reviewer: Do we need this. With constraints not one of these cases will be possible....
            # checks if new pos is too far away
            if abs(self._initial_pos_z - result.best_values['center']) < self._max_offset:
                # checks if new pos is within the scanner range
                if result.best_values['center'] >= self.z_range[0] and result.best_values['center'] <= self.z_range[1]:
                    self.optim_pos_z = result.best_values['center']
                    gauss, params = self._fit_logic.make_gaussianwithslope_model()
                    self.z_fit_data = gauss.eval(
                        x=self._fit_zimage_Z_values, params=result.params)
                else:  # new pos is too far away
                    # checks if new pos is too high
                    if result.best_values['center'] > self._initial_pos_z:
                        if self._initial_pos_z + 0.5 * self.refocus_Z_size <= self.z_range[1]:
                            # moves to higher edge of scan range
                            self.optim_pos_z = self._initial_pos_z + 0.5 * self.refocus_Z_size
                        else:
                            self.optim_pos_z = self.z_range[1]  # moves to highest possible value
                    else:
                        if self._initial_pos_z + 0.5 * self.refocus_Z_size >= self.z_range[0]:
                            # moves to lower edge of scan range
                            self.optim_pos_z = self._initial_pos_z + 0.5 * self.refocus_Z_size
                        else:
                            self.optim_pos_z = self.z_range[0]  # moves to lowest possible value

        self._signal_do_next_optimization_step.emit()

    def finish_refocus(self):
        """ Finishes up and releases hardware after the optimizer scans."""
        self.kill_scanner()

        self.log.info('Optimised from ({0:.3f},{1:.3f},{2:.3f}) to local '
                'maximum at ({3:.3f},{4:.3f},{5:.3f}).'.format(
                    self._initial_pos_x,
                    self._initial_pos_y,
                    self._initial_pos_z,
                    self.optim_pos_x,
                    self.optim_pos_y,
                    self.optim_pos_z))

        # Signal that the optimization has finished, and "return" the optimal position along with caller_tag
        self.signal_refocus_finished.emit(self._caller_tag, [self.optim_pos_x, self.optim_pos_y, self.optim_pos_z, 0])

    def _scan_z_line(self):
        """Scans the z line for refocus."""

        # Moves to the start value of the z-scan
        status = self._move_to_start_pos(
            [self.optim_pos_x,self.optim_pos_y, self._zimage_Z_values[0]])
        if status < 0:
            self.log.error('Error during move to starting point.')
            self.stop_refocus()
            return

        # defining trace of positions for z-refocus
        Z_line = self._zimage_Z_values
        X_line = self.optim_pos_x * np.ones(self._zimage_Z_values.shape)
        Y_line = self.optim_pos_y * np.ones(self._zimage_Z_values.shape)
        A_line = np.zeros(self._zimage_Z_values.shape)

        line = np.vstack((X_line, Y_line, Z_line, A_line))

        # Perform scan
        line_counts = self._scanning_device.scan_line(line)
        if line_counts[0] == -1:
            self.log.error('Z scan went wrong, killing the scanner.')
            self.stop_refocus()
            return

        # Set the data
        self.z_refocus_line = line_counts

        # If subtracting surface, perform a displaced depth line scan
        if self.do_surface_subtraction:
            # Move to start of z-scan
            status = self._move_to_start_pos([
                self.optim_pos_x + self.surface_subtr_scan_offset,
                self.optim_pos_y,
                self._zimage_Z_values[0]])
            if status < 0:
                self.log.error('Error during move to starting point.')
                self.stop_refocus()
                return

            # define an offset line to measure "background"
            line_bg = np.vstack((X_line + self.surface_subtr_scan_offset, Y_line, Z_line, A_line))

            line_bg_counts = self._scanning_device.scan_line(line_bg)
            if line_bg_counts[0] == -1:
                self.log.error('The scan went wrong, killing the scanner.')
                self.stop_refocus()
                return

            # surface-subtracted line scan data is the difference
            self.z_refocus_line = line_counts - line_bg_counts

    def start_scanner(self):
        """Setting up the scanner device.

        @return int: error code (0:OK, -1:error)
        """
        self.lock()
        clock_status = self._scanning_device.set_up_scanner_clock(
            clock_frequency=self._clock_frequency)
        if clock_status < 0:
            self.log.exception('Setting up scanner clock failed.')
            self.unlock()
            return -1

        scanner_status = self._scanning_device.set_up_scanner()
        if scanner_status < 0:
            self.log.exception('Setting up scanner failed.')
            self._scanning_device.close_scanner_clock()
            self.unlock()
            return -1

        return 0

    def kill_scanner(self):
        """Closing the scanner device.

        @return int: error code (0:OK, -1:error)
        """
        try:
            scanner_status = self._scanning_device.close_scanner()
            if scanner_status < 0:
                self.log.exception('Closing refocus scanner failed.')
                return -1
        except:
            self.log.exception('Closing refocus scanner failed.')
            return -1
        try:
            clock_status = self._scanning_device.close_scanner_clock()
            if clock_status < 0:
                self.log.exception('Closing refocus scanner clock failed.')
                return -1
        except:
            self.log.exception('Closing refocus scanner clock failed.')
            return -1
        self.unlock()
        return 0

    def _do_next_optimization_step(self):
        """Handle the steps through the specified optimization sequence
        """

        # At the end fo the sequence, finish the optimization
        if self._optimization_step == len(self.optimization_sequence):
            self._signal_finished_all_optimization_steps.emit()
            return

        # Read the next step in the optimization sequence
        this_step = self.optimization_sequence[self._optimization_step]

        # Increment the step counter
        self._optimization_step += 1

        # Launch the next step
        if this_step == 'XY':
            self._initialize_xy_refocus_image()
            self._signal_scan_next_xy_line.emit()
        elif this_step == 'Z':
            self._initialize_z_refocus_image()
            self._signal_scan_z_line.emit()

    def set_position(self, tag, x=None, y=None, z=None, a=None):

        if x is not None:
            self._current_x = x
        if y is not None:
            self._current_y = y
        if z is not None:
            self._current_z = z
