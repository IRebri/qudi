# -*- coding: utf-8 -*-
"""
This file contains the Qudi counter logic class.

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

from qtpy import QtCore
from collections import OrderedDict
import numpy as np
import time
import matplotlib.pyplot as plt

from logic.generic_logic import GenericLogic
from core.util.mutex import Mutex


class CounterLogic(GenericLogic):
    """ This logic module gathers data from a hardware counting device.

    @signal sigCounterUpdate: there is new counting data available
    @signal sigCountContinuousNext: used to simulate a loop in which the data
                                    acquisition runs.
    @sigmal sigCountGatedNext: ???
    @return error: 0 is OK, -1 is error
    """
    sigCounterUpdated = QtCore.Signal()
    sigCountContinuousNext = QtCore.Signal()
    sigCountGatedNext = QtCore.Signal()

    sigCountFiniteGatedNext = QtCore.Signal()
    sigGatedCounterFinished = QtCore.Signal()
    sigGatedCounterContinue = QtCore.Signal(bool)

    _modclass = 'CounterLogic'
    _modtype = 'logic'

    ## declare connectors
    _in = { 'counter1': 'SlowCounterInterface',
            'savelogic': 'SaveLogic'
            }
    _out = {'counterlogic': 'CounterLogic'}

    def __init__(self, config, **kwargs):
        """ Create CounterLogic object with connectors.

        @param dict config: module configuration
        @param dict kwargs: optional parameters
        """
        super().__init__(config=config, **kwargs)

        #locking for thread safety
        self.threadlock = Mutex()

        self.log.info('The following configuration was found.')

        # checking for the right configuration
        for key in config.keys():
            self.log.info('{0}: {1}'.format(key,config[key]))

        self._count_length = 300 # in bins
        self._count_frequency = 50 # in hertz
        self._counting_samples = 1  # oversampling in bins
        self._smooth_window_length = 10 # in bins
        self._binned_counting = True

        self._counting_mode = 'continuous'


    def on_activate(self, e):
        """ Initialisation performed during activation of the module.

        @param object e: Event class object from Fysom.
                         An object created by the state machine module Fysom,
                         which is connected to a specific event (have a look in
                         the Base Class). This object contains the passed event
                         the state before the event happens and the destination
                         of the state which should be reached after the event
                         has happen.
        """

        # Connect to hardware and save logic
        self._counting_device = self.get_in_connector('counter1')
        self._save_logic = self.get_in_connector('savelogic')

        #Initialize data arrays
        self.countdata = np.zeros((self._count_length,))
        self.countdata_smoothed = np.zeros((self._count_length,))
        # FIXME: Extend to a third, forth.... detector
        # FIXME: Photon source is missleading
        # FIXME: What happens if the rawdata, countdata do not have the same length
        if hasattr(self._counting_device, '_photon_source2'):
            self.countdata2 = np.zeros((self._count_length,))
            self.countdata_smoothed2 = np.zeros((self._count_length,))
        if hasattr(self._counting_device, '_photon_source2'):
            self.rawdata = np.zeros([2, self._counting_samples])
        else:
            self.rawdata = np.zeros([1, self._counting_samples])
        # FIXME: Shouldn't it have the same dimension as the number of detectors
        self._data_to_save=[] # data to save

        # Initialize variables
        self.running = False  # state of counter
        self.stopRequested = False  # state of stoprequest
        self._saving = False  # state of saving
        self._saving_start_time=time.time()  # start time of saving



        #QSignals
        # FIXME: Is it really necessary to have three different Signals?
        # for continuous counting:
        self.sigCountContinuousNext.connect(self.countLoopBody_continuous, QtCore.Qt.QueuedConnection)
        # for gated counting:
        self.sigCountGatedNext.connect(self.countLoopBody_gated, QtCore.Qt.QueuedConnection)
        # for finite gated counting:
        self.sigCountFiniteGatedNext.connect(self.countLoopBody_finite_gated,QtCore.Qt.QueuedConnection)
        return 0

    def on_deactivate(self, e):
        """ Deinitialisation performed during deactivation of the module.

        @param object e: Event class object from Fysom. A more detailed
                         explanation can be found in method activation.
        @return error: 0 is OK, -1 is error
        """
        self.stopCount()
        return_value = 0
        #FIXME: Why 20?
        for ii in range(20):
            if self.getState() == 'idle':
                break
            QtCore.QCoreApplication.processEvents()
            time.sleep(0.1)
            if ii == 20:
                self.log.error('Stopped deactivate counter after trying for 2 seconds!')
                return_value = -1
        return return_value

    # FIXME: Are all these set and get function independent of hardware?

    def set_counting_samples(self, samples = 1):
        """ Sets the oversampling in units of bins.

        @param int samples: oversampling in units of bins.

        @return int: oversampling in units of bins.

        This makes sure, the counter is stopped first and restarted afterwards.
        """
        restart = self.stop_counter()
        self._counting_samples = int(samples)

        # if the counter was running, restart it
        if restart:
            self.startCount()

        return self._counting_samples

    def set_count_length(self, length = 300):
        """ Sets the length of time trace in units of bins.

        @param int length: length of time trace in units of bins.

        @return int: length of time trace in units of bins

        This makes sure, the counter is stopped first and restarted afterwards.
        """
        restart = self.stop_counter()
        self._count_length = int(length)

        # if the counter was running, restart it
        if restart:
            self.startCount()

        return self._count_length

    def set_count_frequency(self, frequency = 50.0):
        """ Sets the frequency with which the data is acquired.

        @param float frequency: the frequency of counting in Hz.

        @return float: the frequency of counting in Hz

        This makes sure, the counter is stopped first and restarted afterwards.
        """
        restart = self.stop_counter()
        self._count_frequency = frequency

        # if the counter was running, restart it
        if restart:
            self.startCount()

        return self._count_frequency


    def get_count_length(self):
        """ Returns the currently set length of the counting array.

        @return int: count_length
        """
        return self._count_length

    def get_count_frequency(self):
        """ Returns the currently set frequency of counting (resolution).

        @return float: count_frequency
        """
        return self._count_frequency

    def get_counting_samples(self):
        """ Returns the currently set number of samples counted per readout.

        @return int: counting_samples
        """
        return self._counting_samples

    def get_saving_state(self):
        """ Returns if the data is saved in the moment.

        @return bool: saving state
        """
        return self._saving

    def start_saving(self, resume=False):
        """ Starts saving the data in a list.

        @return bool: saving state
        """

        if not resume:
            self._data_to_save = []
            self._saving_start_time = time.time()
        self._saving = True

        # If the counter is not running, then it should start running so there is data to save
        if self.isstate('idle'):
            self.startCount()

        return self._saving

    def save_data(self, to_file=True, postfix=''):
        """ Save the counter trace data and writes it to a file.

        @param bool to_file: indicate, whether data have to be saved to file
        @param str postfix: an additional tag, which will be added to the filename upon save

        @return np.array([2 or 3][X]), OrderedDict: array with the
        """
        self._saving = False
        self._saving_stop_time = time.time()

        # write the parameters:
        parameters = OrderedDict()
        parameters['Start counting time (s)'] = time.strftime('%d.%m.%Y %Hh:%Mmin:%Ss', time.localtime(self._saving_start_time))
        parameters['Stop counting time (s)'] = time.strftime('%d.%m.%Y %Hh:%Mmin:%Ss', time.localtime(self._saving_stop_time))
        parameters['Count frequency (Hz)'] = self._count_frequency
        parameters['Oversampling (Samples)'] = self._counting_samples
        parameters['Smooth Window Length (# of events)'] = self._smooth_window_length

        if to_file:
            # If there is a postfix then add separating underscore
            if postfix == '':
                filelabel = 'count_trace'
            else:
                filelabel = 'count_trace_'+postfix

            # prepare the data in a dict or in an OrderedDict:
            data = OrderedDict()
            data = {'Time (s),Signal (counts/s)': self._data_to_save}
            if self._counting_device._photon_source2 is not None:
                data = {'Time (s),Signal 1 (counts/s),Signal 2 (counts/s)': self._data_to_save}

            filepath = self._save_logic.get_path_for_module(module_name='Counter')

            fig = self.draw_figure(data=np.array(self._data_to_save))

            self._save_logic.save_data(data,
                                       filepath,
                                       parameters=parameters,
                                       filelabel=filelabel,
                                       as_text=True,
                                       plotfig=fig
                                       )
            #, as_xml=False, precision=None, delimiter=None)
            plt.close(fig)
            self.log.debug('Counter Trace saved to:\n{0}'.format(filepath))

        return self._data_to_save, parameters

    def draw_figure(self, data):
        """ Draw figure to save with data file.

        @param: nparray data: a numpy array containing counts vs time

        @return: fig fig: a matplotlib figure object to be saved to file.
        """
        # TODO: Draw plot for second APD if it is connected

        count_data = data[:,1]
        time_data = data[:,0]

        # Scale count values using SI prefix
        prefix = ['', 'k', 'M', 'G']
        prefix_index = 0

        while np.max(count_data) > 1000:
            count_data = count_data/1000
            prefix_index = prefix_index + 1

        counts_prefix = prefix[prefix_index]

        # Use qudi style
        plt.style.use(self._save_logic.mpl_qd_style)

        # Create figure
        fig, ax = plt.subplots()

        ax.plot(time_data, count_data, linestyle=':', linewidth=0.5)

        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Fluorescence (' + counts_prefix + 'c/s)')

        return fig

    # FIXME: Check if the following two functions are independent of hardware

    def set_counting_mode(self, mode='continuous'):
        """Set the counting mode, to change between continuous and gated counting.
        Possible options are:
            'continuous'    = counts continuously
            'gated'         = bins the counts according to a gate signal
            'finite-gated'  = finite measurement with predefined number of samples

        @return str: counting mode
        """
        self._counting_mode = mode

        return self._counting_mode

    def get_counting_mode(self):
        """ Retrieve the current counting mode.

        @return str: one of the possible counting options:
                'continuous'    = counts continuously
                'gated'         = bins the counts according to a gate signal
                'finite-gated'  = finite measurement with predefined number of samples
        """
        return self._counting_mode

    # FIXME: Is it really necessary to have 3 different methods here?
    def startCount(self):
        """ This is called externally, and is basically a wrapper that
            redirects to the chosen counting mode start function.

            @return error: 0 is OK, -1 is error
        """

        if self._counting_mode == 'continuous':
            self._startCount_continuous()
            self.log.info('Started continuous counting.')
            return 0
        elif self._counting_mode == 'gated':
            self._startCount_gated()
            self.log.info('Started gated counting.')
            return 0
        elif self._counting_mode == 'finite-gated':
            self._startCount_finite_gated()
            self.log.info('Started finite-gated counting.')
            return 0
        else:
            self.log.error('Unknown counting mode, cannot start the counter.')
            return -1

    def _startCount_continuous(self):
        """Prepare to start counting change state and start counting 'loop'.
        # setting up the counter

        @return error: 0 is OK, -1 is error """

        # set a lock, to signify the measurment is running
        self.lock()

        #FIXME: Instead of clock status I would suggest to get back the clock_frequency
        #FIXME: What about the clock_channel anyway?
        clock_status = self._counting_device.set_up_clock(clock_frequency = self._count_frequency)
        if clock_status < 0:
            self.unlock()
            self.sigCounterUpdated.emit()
            return -1

         #FIXME: Instead of clock status I would suggest to get back the counter_channels and photon sources
        counter_status = self._counting_device.set_up_counter()
        if counter_status < 0:
            clock_closed = self._counting_device.close_clock()
            if clock_closed:
                self.log.info('Clock counter closed')
            self.unlock()
            self.sigCounterUpdated.emit()
            return -1

        # initialising the data arrays
        self.rawdata = np.zeros([2, self._counting_samples])
        self.countdata = np.zeros((self._count_length,))
        self.countdata_smoothed = np.zeros((self._count_length,))
        self._sampling_data = np.empty((self._counting_samples, 2))

        # It is robust to check whether the photon_source2 even exists first.
        if hasattr(self._counting_device, '_photon_source2'):
            if self._counting_device._photon_source2 is not None:
                self.countdata2 = np.zeros((self._count_length,))
                self.countdata_smoothed2 = np.zeros((self._count_length,))
                self._sampling_data2 = np.empty((self._counting_samples, 2))

        self.sigCountContinuousNext.emit()
        return 0

    #FIXME: To Do!
    def _startCount_gated(self):
        """Prepare to start gated counting, and start the loop.
        """
        #eventually:
        #self.sigCountGatedNext.emit()
        pass


    def _startCount_finite_gated(self):
        """Prepare to start finite gated counting.

        @return error: 0 is OK, -1 is error

        Change state and start counting 'loop'."""

        # setting up the counter
        # set a lock, to signify the measurment is running
        self.lock()

        #FIXME: Instead of clock status I would suggest to get back the clock_frequency
        #FIXME: What about the clock_channel anyway?
        returnvalue = self._counting_device.set_up_clock(clock_frequency = self._count_frequency)
        if returnvalue < 0:
            self.unlock()
            self.sigCounterUpdated.emit()
            return -1
         #FIXME: Instead of clock status I would suggest to get back the counter_channels and photon sources
        returnvalue = self._counting_device.set_up_counter(counter_buffer=self._count_length)
        if returnvalue < 0:
            self.unlock()
            self.sigCounterUpdated.emit()
            return -1

        # initialising the data arrays

        # in rawdata the 'fresh counts' are read in
        self.rawdata = np.zeros([2, self._counting_samples])
        # countdata contains the appended data, that is the total displayed counttrace
        self.countdata = np.zeros((self._count_length,))
        # do not use a smoothed count trace
        # self.countdata_smoothed = np.zeros((self._count_length,)) # contains the smoothed data
        # for now, there will be no oversampling mode.
        # self._sampling_data = np.empty((self._counting_samples, 2))

        # the index
        self._already_counted_samples = 0

        self.sigCountFiniteGatedNext.emit()
        return 0

    def stopCount(self):
        """ Set a flag to request stopping counting.
         @return bool: status of stopRequested
        """
        with self.threadlock:
            self.stopRequested = True
        return self.stopRequested

    def stop_counter(self):
        """ Stops the counter if it is running and returns whether it was running.

        @return bool: True if counter was running
        """
        if self.getState() == 'locked':
            restart = True
            self.stopCount()
            while self.getState() == 'locked':
                time.sleep(0.01)
        else:
            restart = False
        return restart

    def countLoopBody_continuous(self):
        """ This method gets the count data from the hardware for the continuous counting mode (default).

        It runs repeatedly in the logic module event loop by being connected
        to sigCountContinuousNext and emitting sigCountContinuousNext through a queued connection.

        @return error: 0 is OK, -1 is error
        """

        # check for aborts of the thread in break if necessary
        if self.stopRequested:
            with self.threadlock:
                try:
                    # close off the actual counter
                    close_counter = self._counting_device.close_counter()
                    if close_counter:
                        self.log.info('Counter closed')
                    clock_closed = self._counting_device.close_clock()
                    if clock_closed:
                        self.log.info('Clock counter closed')

                except Exception as e:
                    self.log.exception('Could not even close the hardware,'
                            ' giving up.')
                    raise e
                    return -1

                finally:
                    # switch the state variable off again
                    self.unlock()
                    self.stopRequested = False
                    self.sigCounterUpdated.emit()

            self.log.info('Counter stopped')
        try:
            # read the current counter value
            self.rawdata = self._counting_device.get_counter(samples=self._counting_samples)

        except Exception as e:
            self.log.error('The counting went wrong, killing the counter.')
            self.stopCount()
            self.sigCountContinuousNext.emit()
            raise e
            return -1

        # remember the new count data in circular array
        self.countdata[0] = np.average(self.rawdata[0])
        # move the array to the left to make space for the new data
        self.countdata = np.roll(self.countdata, -1)
        # also move the smoothing array
        self.countdata_smoothed = np.roll(self.countdata_smoothed, -1)
        # calculate the median and save it
        self.countdata_smoothed[-int(self._smooth_window_length/2)-1:]=np.median(self.countdata[-self._smooth_window_length:])

        # It is robust to check whether the photon_source2 even exists first.
        if hasattr(self._counting_device, '_photon_source2'):
            if self._counting_device._photon_source2 is not None:
                self.countdata2[0] = np.average(self.rawdata[1])
                # move the array to the left to make space for the new data
                self.countdata2 = np.roll(self.countdata2, -1)
                # also move the smoothing array
                self.countdata_smoothed2 = np.roll(self.countdata_smoothed2, -1)
                # calculate the median and save it
                self.countdata_smoothed2[-int(self._smooth_window_length/2)-1:] = np.median(self.countdata2[-self._smooth_window_length:])

        # save the data if necessary
        if self._saving:
             # if oversampling is necessary
            if self._counting_samples > 1:
                if self._counting_device._photon_source2 is not None:
                    self._sampling_data = np.empty([self._counting_samples, 3])
                    self._sampling_data[:, 0] = time.time() - self._saving_start_time
                    self._sampling_data[:, 1] = self.rawdata[0]
                    self._sampling_data[:, 2] = self.rawdata[1]
                else:
                    self._sampling_data = np.empty([self._counting_samples, 2])
                    self._sampling_data[:, 0] = time.time() - self._saving_start_time
                    self._sampling_data[:, 1] = self.rawdata[0]

                self._data_to_save.extend(list(self._sampling_data))
            # if we don't want to use oversampling
            else:
                # append tuple to data stream (timestamp, average counts)
                if self._counting_device._photon_source2 is not None:
                    self._data_to_save.append(np.array(
                                                       (time.time() - self._saving_start_time,
                                                        self.countdata[-1],
                                                        self.countdata2[-1])
                                                        ))
                else:
                    self._data_to_save.append(
                        np.array(
                            (time.time() - self._saving_start_time,
                             self.countdata[-1]
                             )))
        # call this again from event loop
        self.sigCounterUpdated.emit()
        self.sigCountContinuousNext.emit()
        return 0

    #FIXME: To Do!
    def countLoopBody_gated(self):
        """ This method gets the count data from the hardware for the gated
        counting mode.

        It runs repeatedly in the logic module event loop by being connected
        to sigCountGatedNext and emitting sigCountGatedNext through a queued
        connection.

        @return error: 0 is OK, -1 is error
        """

    pass


    # FIXME: I think this all the countloopbody could be combined
    def countLoopBody_finite_gated(self):
        """ This method gets the count data from the hardware for the finite
        gated counting mode.

        It runs repeatedly in the logic module event loop by being connected
        to sigCountFiniteGatedNext and emitting sigCountFiniteGatedNext through
        a queued connection.

        @return error: 0 is OK, -1 is error
        """

        # check for aborts of the thread in break if necessary
        if self.stopRequested:
            with self.threadlock:
                try:
                    # close off the actual counter
                    close_counter = self._counting_device.close_counter()
                    if close_counter:
                        self.log.info('Counter closed')
                    clock_closed = self._counting_device.close_clock()
                    if clock_closed:
                        self.log.info('Clock counter closed')
                except Exception as e:
                    self.log.exception('Could not even close the '
                            'hardware, giving up.')
                    raise e
                    return -1
                finally:
                    # switch the state variable off again
                    self.unlock()
                    self.stopRequested = False
                    self.sigCounterUpdated.emit()
                    self.sigGatedCounterFinished.emit()
                    return
        try:
            # read the current counter value

            self.rawdata = self._counting_device.get_counter(samples=self._counting_samples)

        except Exception as e:
            self.log.error('The counting went wrong, killing the counter.')
            self.stopCount()
            self.sigCountFiniteGatedNext.emit()
            raise e
            return -1


        if self._already_counted_samples+len(self.rawdata[0]) >= len(self.countdata):

            needed_counts = len(self.countdata) - self._already_counted_samples
            self.countdata[0:needed_counts] = self.rawdata[0][0:needed_counts]
            self.countdata=np.roll(self.countdata, -needed_counts)

            self._already_counted_samples = 0
            self.stopRequested = True

        else:
            #self.log.debug(('len(self.rawdata[0]):', len(self.rawdata[0])))
            #self.log.debug(('self._already_counted_samples', self._already_counted_samples))

            # replace the first part of the array with the new data:
            self.countdata[0:len(self.rawdata[0])] = self.rawdata[0]
            # roll the array by the amount of data it had been inserted:
            self.countdata=np.roll(self.countdata, -len(self.rawdata[0]))
            # increment the index counter:
            self._already_counted_samples += len(self.rawdata[0])
            # self.log.debug(('already_counted_samples:',self._already_counted_samples))

        # remember the new count data in circular array
        # self.countdata[0:len(self.rawdata)] = np.average(self.rawdata[0])
        # move the array to the left to make space for the new data
        # self.countdata=np.roll(self.countdata, -1)
        # also move the smoothing array
        # self.countdata_smoothed = np.roll(self.countdata_smoothed, -1)
        # calculate the median and save it
        # self.countdata_smoothed[-int(self._smooth_window_length/2)-1:]=np.median(self.countdata[-self._smooth_window_length:])

        # in this case, saving should happen afterwards, therefore comment out:
        # # save the data if necessary
        # if self._saving:
        #      # if oversampling is necessary
        #     if self._counting_samples > 1:
        #         self._sampling_data=np.empty((self._counting_samples,2))
        #         self._sampling_data[:, 0] = time.time()-self._saving_start_time
        #         self._sampling_data[:, 1] = self.rawdata[0]
        #         self._data_to_save.extend(list(self._sampling_data))
        #     # if we don't want to use oversampling
        #     else:
        #         # append tuple to data stream (timestamp, average counts)
        #         self._data_to_save.append(np.array((time.time()-self._saving_start_time, self.countdata[-1])))
        # # call this again from event loop

        self.sigCounterUpdated.emit()
        self.sigCountFiniteGatedNext.emit()
        return 0

    def save_current_count_trace(self, name_tag=''):
        """ The current displayed counttrace will be saved.

        @param str name_tag: optional, personal description that will be
                             appended to the file name

        This method saves the already displayed counts to file and does not
        accumulate them. The counttrace variable will be saved to file with the
        provided name!
        @return: dict data: Data which was saved
                 str filepath: Filepath
                 dict parameters: Experiment parameters
                 str filelabel: Filelabel
        """

        # If there is a postfix then add separating underscore
        if name_tag == '':
            filelabel = 'snapshot_count_trace'
        else:
            filelabel = 'snapshot_count_trace_'+name_tag

        stop_time = self._count_length/self._count_frequency
        time_step_size = stop_time/len(self.countdata)
        x_axis = np.arange(0, stop_time, time_step_size)

        # prepare the data in a dict or in an OrderedDict:
        data = OrderedDict()
        if hasattr(self._counting_device, '_photon_source2'):
            # if self._counting_device._photon_source2 is None:
            data['Time (s),Signal 1 (counts/s),Signal 2 (counts/s)'] = np.array((x_axis, self.countdata, self.countdata2)).transpose()
        else:
            data['Time (s),Signal (counts/s)'] = np.array((x_axis, self.countdata)).transpose()

        # write the parameters:
        parameters = OrderedDict()
        parameters['Saved at time (s)'] = time.strftime('%d.%m.%Y %Hh:%Mmin:%Ss',
                                                        time.localtime(time.time()))

        parameters['Count frequency (Hz)'] = self._count_frequency
        parameters['Oversampling (Samples)'] = self._counting_samples
        parameters['Smooth Window Length (# of events)'] = self._smooth_window_length

        filepath = self._save_logic.get_path_for_module(module_name='Counter')
        self._save_logic.save_data(data, filepath, parameters=parameters,
                                   filelabel=filelabel, as_text=True)

        #, as_xml=False, precision=None, delimiter=None)
        self.log.debug('Current Counter Trace saved to:\n'
                    '{0}'.format(filepath))

        return data, filepath, parameters, filelabel




