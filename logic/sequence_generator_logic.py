# -*- coding: utf-8 -*-

"""
This file contains the Qudi sequence generator logic for general sequence structure.

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

import numpy as np
import pickle
import os
import time
from qtpy import QtCore
from collections import OrderedDict
import inspect
import importlib

from logic.pulse_objects import PulseBlockElement
from logic.pulse_objects import PulseBlock
from logic.pulse_objects import PulseBlockEnsemble
from logic.pulse_objects import PulseSequence
from logic.generic_logic import GenericLogic
from logic.sampling_functions import SamplingFunctions
from logic.samples_write_methods import SamplesWriteMethods


class SequenceGeneratorLogic(GenericLogic, SamplingFunctions, SamplesWriteMethods):
    """unstable: Nikolas Tomek
    This is the Logic class for the pulse (sequence) generation.

    The basis communication with the GUI should be done as follows:
    The logic holds all the created objects in its internal lists. The GUI is
    able to view this list and get the element of this list.

    How the logic will contruct its objects according to configuration dicts.
    The configuration dicts contain essentially, which parameters of either the
    PulseBlockElement objects or the PulseBlock objects can be changed and
    set via the GUI.

    In the end the information transfer happend through lists (read by the GUI)
    and dicts (set by the GUI). The logic sets(creats) the objects in the list
    and read the dict, which tell it which parameters to expect from the GUI.
    """

    _modclass = 'sequencegeneratorlogic'
    _modtype = 'logic'

    ## declare connectors
    _out = {'sequencegenerator': 'SequenceGeneratorLogic'}


    # define signals
    sigBlockDictUpdated = QtCore.Signal(dict)
    sigEnsembleDictUpdated = QtCore.Signal(dict)
    sigSequenceDictUpdated = QtCore.Signal(dict)
    sigSampleEnsembleComplete = QtCore.Signal(str)
    sigSampleSequenceComplete = QtCore.Signal(str)
    sigCurrentBlockUpdated = QtCore.Signal(object)
    sigCurrentEnsembleUpdated = QtCore.Signal(object)
    sigCurrentSequenceUpdated = QtCore.Signal(object)
    sigSettingsUpdated = QtCore.Signal(list, str, float, dict, str)
    sigPredefinedSequencesUpdated = QtCore.Signal(dict)
    sigPredefinedSequenceGenerated = QtCore.Signal(str)

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self.log.info('The following configuration was found.')

        # checking for the right configuration
        for key in config.keys():
            self.log.info('{0}: {1}'.format(key,config[key]))

        # Get all the attributes from the SamplingFunctions module:
        SamplingFunctions.__init__(self)
        # Get all the attributes from the SamplesWriteMethods module:
        SamplesWriteMethods.__init__(self)

        # here the currently shown data objects of the editors should be stored
        self.current_block = None
        self.current_ensemble = None
        self.current_sequence = None

        # The created PulseBlock objects are saved in this dictionary. The keys are the names.
        self.saved_pulse_blocks = OrderedDict()
        # The created PulseBlockEnsemble objects are saved in this dictionary.
        # The keys are the names.
        self.saved_pulse_block_ensembles = OrderedDict()
        # The created Sequence objects are saved in this dictionary. The keys are the names.
        self.saved_pulse_sequences = OrderedDict()

        if 'pulsed_file_dir' in config.keys():
            self.pulsed_file_dir = config['pulsed_file_dir']
            if not os.path.exists(self.pulsed_file_dir):
                homedir = self.get_home_dir()
                self.pulsed_file_dir = os.path.join(homedir, 'pulsed_files')
                self.log.warning('The directort defined in "pulsed_file_dir" '
                        'in the config for SequenceGeneratorLogic class does '
                        'not exist!\n'
                        'The default home directory\n{0}\n will be '
                        'taken instead.'.format(self.pulsed_file_dir))
        else:
            homedir = self.get_home_dir()
            self.pulsed_file_dir = os.path.join(homedir, 'pulsed_files')
            self.log.warning('No directory with the attribute '
                    '"pulsed_file_dir" is defined for the '
                    'SequenceGeneratorLogic!\n'
                    'The default home directory\n{0}\n will be taken '
                    'instead.'.format(self.pulsed_file_dir))


        self.block_dir = self._get_dir_for_name('pulse_block_objects')
        self.ensemble_dir = self._get_dir_for_name('pulse_ensemble_objects')
        self.sequence_dir = self._get_dir_for_name('sequence_objects')
        self.waveform_dir = self._get_dir_for_name('sampled_hardware_files')
        self.temp_dir = self._get_dir_for_name('temporary_files')

        # Information on used channel configuration for sequence generation
        # IMPORTANT: THIS CONFIG DOES NOT REPRESENT THE ACTUAL SETTINGS ON THE HARDWARE
        self.analog_channels = 2
        self.digital_channels = 4
        self.activation_config = ['a_ch1', 'd_ch1', 'd_ch2', 'a_ch2', 'd_ch3', 'd_ch4']
        self.laser_channel = 'd_ch1'
        self.amplitude_dict = OrderedDict({'a_ch1': 0.5, 'a_ch2': 0.5, 'a_ch3': 0.5, 'a_ch4': 0.5})
        self.sample_rate = 25e9
        # The file format for the sampled hardware-compatible waveforms and sequences
        self.waveform_format = 'wfmx' # can be 'wfmx', 'wfm' or 'fpga'
        self.sequence_format = 'seqx' # can be 'seqx' or 'seq'

        # a dictionary with all predefined generator methods and measurement sequence names
        self.generate_methods = None


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
        self._get_blocks_from_file()
        self._get_ensembles_from_file()
        self._get_sequences_from_file()

        self._attach_predefined_methods()

        if 'activation_config' in self._statusVariables:
            self.activation_config = self._statusVariables['activation_config']
        if 'laser_channel' in self._statusVariables:
            self.laser_channel = self._statusVariables['laser_channel']
        if 'amplitude_dict' in self._statusVariables:
            self.amplitude_dict = self._statusVariables['amplitude_dict']
        if 'sample_rate' in self._statusVariables:
            self.sample_rate = self._statusVariables['sample_rate']
        if 'waveform_format' in self._statusVariables:
            self.waveform_format = self._statusVariables['waveform_format']
        if 'sequence_format' in self._statusVariables:
            self.sequence_format = self._statusVariables['sequence_format']
        self.analog_channels = len([chnl for chnl in self.activation_config if 'a_ch' in chnl])
        self.digital_channels = len([chnl for chnl in self.activation_config if 'd_ch' in chnl])
        self.sigSettingsUpdated.emit(self.activation_config, self.laser_channel, self.sample_rate,
                                     self.amplitude_dict,
                                     self.waveform_format + '/' + self.sequence_format)

    def on_deactivate(self, e):
        """ Deinitialisation performed during deactivation of the module.

        @param object e: Event class object from Fysom. A more detailed
                         explanation can be found in method activation.
        """
        self._statusVariables['activation_config'] = self.activation_config
        self._statusVariables['laser_channel'] = self.laser_channel
        self._statusVariables['amplitude_dict'] = self.amplitude_dict
        self._statusVariables['sample_rate'] = self.sample_rate
        self._statusVariables['waveform_format'] = self.waveform_format
        self._statusVariables['sequence_format'] = self.sequence_format

    def _attach_predefined_methods(self):
        """
        Retrieve in the folder all files for predefined methods and attach their methods to the

        @return:
        """
        self.generate_methods = OrderedDict()
        filename_list = []
        # The assumption is that in the directory predefined_methods, there are
        # *.py files, which contain only methods!
        path = os.path.join(self.get_main_dir(), 'logic', 'predefined_methods')
        for entry in os.listdir(path):
            if os.path.isfile(os.path.join(path, entry)) and entry.endswith('.py'):
                filename_list.append(entry[:-3])

        for filename in filename_list:
            mod = importlib.import_module('logic.predefined_methods.{0}'.format(filename))
            for method in dir(mod):
                try:
                    # Check for callable function or method:
                    ref = getattr(mod, method)
                    if callable(ref) and (inspect.ismethod(ref) or inspect.isfunction(ref)):
                        # Bind the method as an attribute to the Class
                        setattr(SequenceGeneratorLogic, method, getattr(mod, method))
                        # Add method to dictionary if it is a generator method
                        if method.startswith('generate_'):
                            self.generate_methods[method[9:]] = eval('self.'+method)
                except:
                    self.log.error('It was not possible to import element {0} from {1} into '
                                   'SequenceGenerationLogic.'.format(method, filename))
        self.sigPredefinedSequencesUpdated.emit(self.generate_methods)
        return

    def _get_dir_for_name(self, name):
        """ Get the path to the pulsed sub-directory 'name'.

        @param str name: name of the folder
        @return: str, absolute path to the directory with folder 'name'.
        """
        path = os.path.join(self.pulsed_file_dir, name)
        if not os.path.exists(path):
            os.makedirs(os.path.abspath(path))
        return os.path.abspath(path)

    def request_init_values(self):
        """

        @return:
        """
        self.sigBlockDictUpdated.emit(self.saved_pulse_blocks)
        self.sigEnsembleDictUpdated.emit(self.saved_pulse_block_ensembles)
        self.sigSequenceDictUpdated.emit(self.saved_pulse_sequences)
        self.sigSampleEnsembleComplete.emit('')
        self.sigSampleSequenceComplete.emit('')
        self.sigCurrentBlockUpdated.emit(self.current_block)
        self.sigCurrentEnsembleUpdated.emit(self.current_ensemble)
        self.sigCurrentSequenceUpdated.emit(self.current_sequence)
        self.sigSettingsUpdated.emit(self.activation_config, self.laser_channel, self.sample_rate,
                                     self.amplitude_dict,
                                     self.waveform_format + '/' + self.sequence_format)
        self.sigPredefinedSequencesUpdated.emit(self.generate_methods)
        return

    def set_settings(self, activation_config, laser_channel, sample_rate, amplitude_dict, sampling_format):
        """
        Sets all settings for the generator logic.

        @param activation_config:
        @param laser_channel:
        @param sample_rate:
        @param amplitude_dict:
        @param sampling_format:
        @return:
        """
        # check if the currently chosen laser channel is part of the config and adjust if this
        # is not the case. Choose first digital channel in that case.
        if laser_channel not in activation_config:
            laser_channel = None
            for channel in activation_config:
                if 'd_ch' in channel:
                    laser_channel = channel
                    break
            if laser_channel is None:
                self.log.warning('No digital channel present in sequence generator activation '
                                 'config.')
        self.laser_channel = laser_channel
        self.activation_config = activation_config
        self.analog_channels = len([chnl for chnl in activation_config if 'a_ch' in chnl])
        self.digital_channels = len([chnl for chnl in activation_config if 'd_ch' in chnl])
        self.amplitude_dict = amplitude_dict
        self.sample_rate = sample_rate
        self.waveform_format = sampling_format.split('/')[0]
        self.sequence_format = sampling_format.split('/')[1]
        self.sigSettingsUpdated.emit(activation_config, laser_channel, sample_rate, amplitude_dict,
                                     sampling_format)
        return self.activation_config, self.laser_channel, self.sample_rate, self.amplitude_dict, \
               sampling_format

# -----------------------------------------------------------------------------
#                    BEGIN sequence/block generation
# -----------------------------------------------------------------------------
    def get_saved_asset(self, name):
        """
        Returns the data object for a saved Ensemble/Sequence with name "name". Searches in the
        saved assets for a Sequence object first. If no Sequence by that name could be found search
        for Ensembles instead. If neither could be found return None.
        @param name: Name of the Sequence/Ensemble
        @return: PulseSequence | PulseBlockEnsemble | None
        """
        if name == '':
            asset_obj = None
        elif name in list(self.saved_pulse_sequences):
            asset_obj = self.saved_pulse_sequences[name]
        elif name in list(self.saved_pulse_block_ensembles):
            asset_obj = self.saved_pulse_block_ensembles[name]
        else:
            asset_obj = None
            self.log.warning('No PulseSequence or PulseBlockEnsemble by the name "{0}" could be '
                             'found in saved assets. Returning None.'.format(name))
        return asset_obj


    def save_block(self, name, block):
        """ Serialize a PulseBlock object to a *.blk file.

        @param name: string, name of the block to save
        @param block: PulseBlock object which will be serialized
        """
        # TODO: Overwrite handling
        block.name = name
        self.current_block = block
        self.saved_pulse_blocks[name] = block
        self._save_blocks_to_file()
        self.sigBlockDictUpdated.emit(self.saved_pulse_blocks)
        self.sigCurrentBlockUpdated.emit(self.current_block)
        return

    def load_block(self, name):
        """

        @param name:
        @return:
        """
        if name not in self.saved_pulse_blocks:
            self.log.error('PulseBlock "{0}" could not be found in saved pulse blocks. Load failed.'
                           ''.format(name))
            return
        block = self.saved_pulse_blocks[name]
        self.current_block = block
        self.sigCurrentBlockUpdated.emit(self.current_block)
        return

    def delete_block(self, name):
        """ Remove the serialized object "name" from the block list and HDD.

        @param name: string, name of the PulseBlock object to be removed.
        """
        if name in list(self.saved_pulse_blocks):
            del(self.saved_pulse_blocks[name])
            if hasattr(self.current_block, 'name'):
                if self.current_block.name == name:
                    self.current_block = None
                    self.sigCurrentBlockUpdated.emit(self.current_block)
            self._save_blocks_to_file()
            self.sigBlockDictUpdated.emit(self.saved_pulse_blocks)
        else:
            self.log.warning('PulseBlock object with name "{0}" not found in saved '
                             'blocks.\nTherefore nothing is removed.'.format(name))
        return

    def _get_blocks_from_file(self):
        """ Update the saved_pulse_block dict from file """
        block_files = [f for f in os.listdir(self.block_dir) if 'block_dict.blk' in f]
        if len(block_files) == 0:
            self.log.warning('No serialized block dict was found in {0}.'.format(self.block_dir))
            self.saved_pulse_blocks = OrderedDict()
            self.sigBlockDictUpdated.emit(self.saved_pulse_blocks)
            return
        # raise error if more than one file is present
        if len(block_files) > 1:
            self.log.error('More than one serialized block dict was found in {0}.\n'
                           'Using {1}.'.format(self.block_dir, block_files[-1]))
        block_files = block_files[-1]
        try:
            with open(os.path.join(self.block_dir, block_files), 'rb') as infile:
                self.saved_pulse_blocks = pickle.load(infile)
        except:
            self.saved_pulse_blocks = OrderedDict()
            self.log.error('Failed to deserialize ensemble dict "{0}" from "{1}".'
                           ''.format(block_files, self.block_dir))
        self.sigBlockDictUpdated.emit(self.saved_pulse_blocks)
        return

    def _save_blocks_to_file(self):
        """ Saves the saved_pulse_block dict to file """
        try:
            with open(os.path.join(self.block_dir, 'block_dict.blk.tmp'), 'wb') as outfile:
                pickle.dump(self.saved_pulse_blocks, outfile)
        except:
            self.log.error('Failed to serialize ensemble dict in "{0}".'
                           ''.format(os.path.join(self.block_dir, 'block_dict.blk.tmp')))
            return
        # remove old file and rename temp file
        try:
            os.rename(os.path.join(self.block_dir, 'block_dict.blk.tmp'),
                      os.path.join(self.block_dir, 'block_dict.blk'))
        except WindowsError:
            os.remove(os.path.join(self.block_dir, 'block_dict.blk'))
            os.rename(os.path.join(self.block_dir, 'block_dict.blk.tmp'),
                      os.path.join(self.block_dir, 'block_dict.blk'))
        return

    def save_ensemble(self, name, ensemble):
        """ Saves a PulseBlockEnsemble with name name to file.

        @param str name: name of the ensemble, which will be serialized.
        @param obj ensemble: a PulseBlockEnsemble object
        """
        # TODO: Overwrite handling
        ensemble.name = name
        self.current_ensemble = ensemble
        self.saved_pulse_block_ensembles[name] = ensemble
        self._save_ensembles_to_file()
        self.sigEnsembleDictUpdated.emit(self.saved_pulse_block_ensembles)
        self.sigCurrentEnsembleUpdated.emit(self.current_ensemble)
        return

    def load_ensemble(self, name):
        """

        @param name:
        @return:
        """
        if name not in self.saved_pulse_block_ensembles:
            self.log.error('PulseBlockEnsemble "{0}" could not be found in saved pulse block '
                           'ensembles. Load failed.'.format(name))
            return
        ensemble = self.saved_pulse_block_ensembles[name]
        # set generator settings if found in ensemble metadata
        if ensemble.sample_rate is not None:
            self.sample_rate = ensemble.sample_rate
        if ensemble.amplitude_dict is not None:
            self.amplitude_dict = ensemble.amplitude_dict
        if ensemble.activation_config is not None:
            self.activation_config = ensemble.activation_config
        if ensemble.laser_channel is not None:
            self.laser_channel = ensemble.laser_channel
        self.sigSettingsUpdated.emit(self.activation_config, self.laser_channel, self.sample_rate,
                                     self.amplitude_dict,
                                     self.waveform_format + '/' + self.sequence_format)
        self.current_ensemble = ensemble
        self.sigCurrentEnsembleUpdated.emit(ensemble)
        return

    def delete_ensemble(self, name):
        """ Remove the ensemble with 'name' from the ensemble list and HDD. """
        if name in list(self.saved_pulse_block_ensembles):
            del(self.saved_pulse_block_ensembles[name])
            if hasattr(self.current_ensemble, 'name'):
                if self.current_ensemble.name == name:
                    self.current_ensemble = None
                    self.sigCurrentEnsembleUpdated.emit(self.current_ensemble)
            self._save_ensembles_to_file()
            self.sigEnsembleDictUpdated.emit(self.saved_pulse_block_ensembles)
        else:
            self.log.warning('PulseBlockEnsemble object with name "{0}" not found in saved '
                             'ensembles.\nTherefore nothing is removed.'.format(name))
        return

    def _get_ensembles_from_file(self):
        """ Update the saved_pulse_block_ensembles dict from file """
        ensemble_files = [f for f in os.listdir(self.ensemble_dir) if 'ensemble_dict.ens' in f]
        if len(ensemble_files) == 0:
            self.log.warning('No serialized ensembles dict was found in {0}.'
                             ''.format(self.ensemble_dir))
            self.saved_pulse_block_ensembles = OrderedDict()
            self.sigEnsembleDictUpdated.emit(self.saved_pulse_block_ensembles)
            return
        # raise error if more than one file is present
        if len(ensemble_files) > 1:
            self.log.error('More than one serialized ensemble dict was found in {0}.\n'
                           'Using {1}.'.format(self.ensemble_dir, ensemble_files[-1]))
        ensemble_files = ensemble_files[-1]
        try:
            with open(os.path.join(self.ensemble_dir, ensemble_files), 'rb') as infile:
                self.saved_pulse_block_ensembles = pickle.load(infile)
        except:
            self.saved_pulse_block_ensembles = OrderedDict()
            self.log.error('Failed to deserialize ensemble dict "{0}" from "{1}".'
                           ''.format(ensemble_files, self.ensemble_dir))
        self.sigEnsembleDictUpdated.emit(self.saved_pulse_block_ensembles)
        return

    def _save_ensembles_to_file(self):
        """ Saves the saved_pulse_block_ensembles dict to file """
        try:
            with open(os.path.join(self.ensemble_dir, 'ensemble_dict.ens.tmp'), 'wb') as outfile:
                pickle.dump(self.saved_pulse_block_ensembles, outfile)
        except:
            self.log.error('Failed to serialize ensemble dict in "{0}".'
                           ''.format(os.path.join(self.ensemble_dir, 'ensemble_dict.ens.tmp')))
            return
        # remove old file and rename temp file
        try:
            os.rename(os.path.join(self.ensemble_dir, 'ensemble_dict.ens.tmp'),
                      os.path.join(self.ensemble_dir, 'ensemble_dict.ens'))
        except WindowsError:
            os.remove(os.path.join(self.ensemble_dir, 'ensemble_dict.ens'))
            os.rename(os.path.join(self.ensemble_dir, 'ensemble_dict.ens.tmp'),
                      os.path.join(self.ensemble_dir, 'ensemble_dict.ens'))
        return

    def save_sequence(self, name, sequence):
        """ Serialize the PulseSequence object with name 'name' to file.

        @param str name: name of the sequence object.
        @param object sequence: a PulseSequence object, which is going to be
                                serialized to file.

        @return: str: name of the serialized object, if needed.
        """
        # TODO: Overwrite handling
        sequence.name = name
        self.current_sequence = sequence
        self.saved_pulse_sequences[name] = sequence
        self._save_sequences_to_file()
        self.sigSequenceDictUpdated.emit(self.saved_pulse_sequences)
        self.sigCurrentSequenceUpdated.emit(self.current_sequence)

    def load_sequence(self, name):
        """

        @param name:
        @return:
        """
        if name not in self.saved_pulse_sequences:
            self.log.error('PulseSequence "{0}" could not be found in saved pulse sequences. '
                           'Load failed.'.format(name))
            return
        sequence = self.saved_pulse_sequences[name]
        # set generator settings if found in seqeunce metadata
        if sequence.sample_rate is not None:
            self.sample_rate = sequence.sample_rate
        if sequence.amplitude_dict is not None:
            self.amplitude_dict = sequence.amplitude_dict
        if sequence.activation_config is not None:
            self.activation_config = sequence.activation_config
        if sequence.laser_channel is not None:
            self.laser_channel = sequence.laser_channel
        self.sigSettingsUpdated.emit(self.activation_config, self.laser_channel, self.sample_rate,
                                     self.amplitude_dict,
                                     self.waveform_format + '/' + self.sequence_format)
        self.current_sequence = sequence
        self.sigCurrentSequenceUpdated.emit(sequence)
        return

    def delete_sequence(self, name):
        """ Remove the sequence "name" from the sequence list and HDD.

        @param str name: name of the sequence object, which should be deleted.
        """
        if name in list(self.saved_pulse_sequences):
            del(self.saved_pulse_sequences[name])
            if hasattr(self.current_sequence, 'name'):
                if self.current_sequence.name == name:
                    self.current_sequence = None
                    self.sigCurrentSequenceUpdated.emit(self.current_sequence)
            self._save_sequences_to_file()
            self.sigSequenceDictUpdated.emit(self.saved_pulse_sequences)
        else:
            self.log.warning('PulseBlockEnsemble object with name "{0}" not found in saved '
                             'ensembles.\nTherefore nothing is removed.'.format(name))
        return

    def generate_predefined_sequence(self, predefined_sequence_name, args):
        """

        @param predefined_sequence_name:
        @param args:
        @return:
        """
        gen_method = self.generate_methods[predefined_sequence_name]
        try:
            gen_method(*args)
        except:
            self.log.error('Generation of predefined sequence "{0}" failed.'
                           ''.format(predefined_sequence_name))
            return
        self.sigPredefinedSequenceGenerated.emit(predefined_sequence_name)
        return

    def _get_sequences_from_file(self):
        """ Update the saved_pulse_sequences dict from file """
        sequence_files = [f for f in os.listdir(self.sequence_dir) if 'sequence_dict.sequ' in f]
        if len(sequence_files) == 0:
            self.log.warning('No serialized sequence dict was found in {0}.'
                             ''.format(self.sequence_dir))
            self.saved_pulse_sequences = OrderedDict()
            self.sigSequenceDictUpdated.emit(self.saved_pulse_sequences)
            return
        # raise error if more than one file is present
        if len(sequence_files) > 1:
            self.log.error('More than one serialized sequence dict was found in {0}.\n'
                           'Using {1}.'.format(self.sequence_dir, sequence_files[-1]))
        sequence_files = sequence_files[-1]
        try:
            with open(os.path.join(self.sequence_dir, sequence_files), 'rb') as infile:
                self.saved_pulse_sequences = pickle.load(infile)
        except:
            self.saved_pulse_sequences = OrderedDict()
            self.log.error('Failed to deserialize sequence dict "{0}" from "{1}".'
                           ''.format(sequence_files, self.sequence_dir))
        self.sigSequenceDictUpdated.emit(self.saved_pulse_sequences)
        return

    def _save_sequences_to_file(self):
        """ Saves the saved_pulse_sequences dict to file """
        try:
            with open(os.path.join(self.sequence_dir, 'sequence_dict.sequ.tmp'), 'wb') as outfile:
                pickle.dump(self.saved_pulse_sequences, outfile)
        except:
            self.log.error('Failed to serialize ensemble dict in "{0}".'
                           ''.format(os.path.join(self.sequence_dir, 'sequence_dict.sequ.tmp')))
            return
        # remove old file and rename temp file
        try:
            os.rename(os.path.join(self.sequence_dir, 'sequence_dict.sequ.tmp'),
                      os.path.join(self.sequence_dir, 'sequence_dict.sequ'))
        except WindowsError:
            os.remove(os.path.join(self.sequence_dir, 'sequence_dict.sequ'))
            os.rename(os.path.join(self.sequence_dir, 'sequence_dict.sequ.tmp'),
                      os.path.join(self.sequence_dir, 'sequence_dict.sequ'))
        return

    #---------------------------------------------------------------------------
    #                    END sequence/block generation
    #---------------------------------------------------------------------------


    #---------------------------------------------------------------------------
    #                    BEGIN sequence/block sampling
    #---------------------------------------------------------------------------
    def _analyze_block_ensemble(self, ensemble):
        """

        @param ensemble:
        @return:
        """
        state_length_bins_arr = np.array([], dtype=int)
        number_of_elements = 0
        for block, reps in ensemble.block_list:
            number_of_elements += (reps+1)*len(block.element_list)
            num_state_changes = (reps+1) * len(block.element_list)
            tmp_length_bins = np.zeros(num_state_changes, dtype=int)
            # Iterate over all repertitions of the current block
            state_index = 0
            for rep_no in range(reps+1):
                # Iterate over the Block_Elements inside the current block
                for elem_index, block_element in enumerate(block.element_list):
                    init_length_s = block_element.init_length_s
                    increment_s = block_element.increment_s
                    element_length_s = init_length_s + (rep_no * increment_s)
                    tmp_length_bins[state_index] = int(np.rint(element_length_s * self.sample_rate))
                    state_index += 1
            state_length_bins_arr = np.append(state_length_bins_arr, tmp_length_bins)
        number_of_samples = np.sum(state_length_bins_arr)
        number_of_states = len(state_length_bins_arr)
        return number_of_samples, number_of_elements, number_of_states, state_length_bins_arr

    def sample_pulse_block_ensemble(self, ensemble_name, write_to_file=True, chunkwise=True,
                                    offset_bin=0, name_tag=''):
        """ General sampling of a PulseBlockEnsemble object, which serves as the construction plan.

        @param str ensemble_name: Name, which should correlate with the name of on of the displayed
                                  ensembles.
        @param bool write_to_file: Write either to RAM or to File (depends on the available space
                                   in RAM). If set to FALSE, this method will return the samples
                                   (digital and analog) as numpy arrays
        @param bool chunkwise: Decide, whether you want to write chunkwise, which will reduce
                               memory usage but will increase vastly the amount of time needed.
        @param int offset_bin: If many pulse ensembles are samples sequentially, then the
                               offset_bin of the previous sampling can be passed to maintain
                               rotating frame across pulse_block_ensembles
        @param str name_tag: a name tag, which is used to keep the sampled files together, which
                             where sampled from the same PulseBlockEnsemble object but where
                             different offset_bins were used.

        @return tuple: of length 4 with
                       (analog_samples, digital_samples, [<created_files>], offset_bin).
                        analog_samples:
                            numpy arrays containing the sampled voltages
                        digital_samples:
                            numpy arrays containing the sampled logic levels
                        [<created_files>]:
                            list of strings, with the actual created files through the pulsing
                            device
                        offset_bin:
                            integer, which is used for maintaining the rotation frame.

        This method is creating the actual samples (voltages and logic states) for each time step
        of the analog and digital channels specified in the PulseBlockEnsemble.
        Therefore it iterates through all blocks, repetitions and elements of the ensemble and
        calculates the exact voltages (float64) according to the specified math_function. The
        samples are later on stored inside a float32 array.
        So each element is calculated with high precision (float64) and then down-converted to
        float32 to be stored.

        To preserve the rotating frame, an offset counter is used to indicate the absolute time
        within the ensemble. All calculations are done with time bins (dtype=int) to avoid rounding
        errors. Only in the last step when a single PulseBlockElement object is sampled  these
        integer bin values are translated into a floating point time.

        The chunkwise write mode is used to save memory usage at the expense of time. Here for each
        PulseBlockElement the write_to_file method in the HW module is called to avoid large
        arrays inside the memory. In other words: The whole sample arrays are never created at any
        time. This results in more function calls and general overhead causing the much longer time
        to complete.
        """
        # lock module if it's not already locked (sequence sampling in progress)
        if self.getState() == 'idle':
            self.lock()
            sequence_sampling_in_progress = False
        else:
            sequence_sampling_in_progress = True
        # check for old files associated with the new ensemble and delete them from host PC
        if write_to_file:
            # get sampled filenames on host PC referring to the same ensemble
            filename_list = [f for f in os.listdir(self.waveform_dir) if
                             f.startswith(ensemble_name + '_ch')]
            # delete all filenames in the list
            for file in filename_list:
                os.remove(os.path.join(self.waveform_dir, file))

            if len(filename_list) != 0:
                self.log.info('Found old sampled ensembles for name "{0}". Files deleted before '
                              'sampling: {1}'.format(ensemble_name, filename_list))

        start_time = time.time()
        # get ensemble
        ensemble = self.saved_pulse_block_ensembles[ensemble_name]
        # Ensemble parameters to determine the shape of sample arrays
        ana_channels = ensemble.analog_channels
        dig_channels = ensemble.digital_channels
        ana_chnl_names = [chnl for chnl in self.activation_config if 'a_ch' in chnl]
        if self.digital_channels != dig_channels or self.analog_channels != ana_channels:
            self.log.error('Sampling of PulseBlockEnsemble "{0}" failed!\nMismatch in number of '
                           'analog and digital channels between logic ({1}, {2}) and '
                           'PulseBlockEnsemble ({3}, {4}).'
                           ''.format(ensemble_name, self.analog_channels, self.digital_channels,
                                     ana_channels, dig_channels))
            return [], [], [''], 0

        number_of_samples, number_of_elements, number_of_states, state_length_bins_arr = self._analyze_block_ensemble(ensemble)
        # The time bin offset for each element to be sampled to preserve rotating frame.
        if chunkwise and write_to_file:
            # Flags and counter for chunkwise writing
            is_first_chunk = True
            is_last_chunk = False
            element_count = 0
        else:
            # Allocate huge sample arrays if chunkwise writing is disabled.
            analog_samples = np.empty([ana_channels, number_of_samples], dtype = 'float32')
            digital_samples = np.empty([dig_channels, number_of_samples], dtype = bool)
            # Starting index for the sample array entrys
            entry_ind = 0

        # Iterate over all blocks within the PulseBlockEnsemble object
        for block, reps in ensemble.block_list:
            # Iterate over all repertitions of the current block
            for rep_no in range(reps+1):
                # Iterate over the Block_Elements inside the current block
                for elem_ind, block_element in enumerate(block.element_list):
                    parameters = block_element.parameters
                    init_length_s = block_element.init_length_s
                    increment_s = block_element.increment_s
                    digital_high = block_element.digital_high
                    pulse_function = block_element.pulse_function
                    element_length_s = init_length_s + (rep_no*increment_s)
                    element_length_bins = int(np.rint(element_length_s * self.sample_rate))

                    # create floating point time array for the current element inside rotating frame
                    time_arr = (offset_bin + np.arange(element_length_bins, dtype='float64')) / self.sample_rate

                    if chunkwise and write_to_file:
                        # determine it the current element is the last one to be sampled.
                        # Toggle the is_last_chunk flag accordingly.
                        element_count += 1
                        if element_count == number_of_elements:
                            is_last_chunk = True

                        # allocate temporary sample arrays to contain the current element
                        analog_samples = np.empty([ana_channels, element_length_bins], dtype='float32')
                        digital_samples = np.empty([dig_channels, element_length_bins], dtype=bool)

                        # actually fill the allocated sample arrays with values.
                        for i, state in enumerate(digital_high):
                            digital_samples[i] = np.full(element_length_bins, state, dtype=bool)
                        for i, func_name in enumerate(pulse_function):
                            analog_samples[i] = np.float32(self._math_func[func_name](time_arr, parameters[i])/self.amplitude_dict[ana_chnl_names[i]])

                        # write temporary sample array to file
                        created_files = self._write_to_file[self.waveform_format](
                            ensemble.name + name_tag, analog_samples, digital_samples,
                            number_of_samples, is_first_chunk, is_last_chunk)
                        # set flag to FALSE after first write
                        is_first_chunk = False
                    else:
                        # if the ensemble should be sampled as a whole (chunkwise = False) fill the
                        # entries in the huge sample arrays
                        for i, state in enumerate(digital_high):
                            digital_samples[i, entry_ind:entry_ind+element_length_bins] = np.full(element_length_bins, state, dtype=bool)
                        for i, func_name in enumerate(pulse_function):
                            analog_samples[i, entry_ind:entry_ind+element_length_bins] = np.float32(self._math_func[func_name](time_arr, parameters[i])/self.amplitude_dict[ana_chnl_names[i]])

                        # increment the index offset of the overall sample array for the next
                        # element
                        entry_ind += element_length_bins

                    # if the rotating frame should be preserved (default) increment the offset
                    # counter for the time array.
                    if ensemble.rotating_frame:
                        offset_bin += element_length_bins

        if not write_to_file:
            # return a status message with the time needed for sampling the entire ensemble as a
            # whole without writing to file.
            self.log.info('Time needed for sampling and writing PulseBlockEnsemble to file as a '
                          'whole: {0} sec.'.format(int(np.rint(time.time() - start_time))))
            # return the sample arrays for write_to_file was set to FALSE
            if not sequence_sampling_in_progress:
                self.unlock()
                self.sigSampleEnsembleComplete.emit(ensemble_name)
            return analog_samples, digital_samples, created_files, offset_bin
        elif chunkwise:
            # return a status message with the time needed for sampling and writing the ensemble
            # chunkwise.
            self.log.info('Time needed for sampling and writing to file chunkwise: {0} sec'
                          ''.format(int(np.rint(time.time()-start_time))))
            if not sequence_sampling_in_progress:
                self.unlock()
                self.sigSampleEnsembleComplete.emit(ensemble_name)
            return [], [], created_files, offset_bin
        else:
            # If the sampling should not be chunkwise and write to file is enabled call the
            # write_to_file method only once with both flags set to TRUE
            is_first_chunk = True
            is_last_chunk = True
            created_files = self._write_to_file[self.waveform_format](ensemble.name + name_tag,
                                                                      analog_samples,
                                                                      digital_samples,
                                                                      number_of_samples,
                                                                      is_first_chunk, is_last_chunk)
            # return a status message with the time needed for sampling and writing the ensemble as
            # a whole.
            self.log.info('Time needed for sampling and writing PulseBlockEnsemble to file as a '
                          'whole: {0} sec'.format(int(np.rint(time.time()-start_time))))
            if not sequence_sampling_in_progress:
                self.unlock()
                self.sigSampleEnsembleComplete.emit(ensemble_name)
            return [], [], created_files, offset_bin

    def sample_pulse_sequence(self, sequence_name, write_to_file=True, chunkwise=True):
        """ Samples the PulseSequence object, which serves as the construction plan.

        @param str ensemble_name: Name, which should correlate with the name of on of the displayed
                                  ensembles.
        @param bool write_to_file: Write either to RAM or to File (depends on the available space
                                   in RAM). If set to FALSE, this method will return the samples
                                   (digital and analog) as numpy arrays
        @param bool chunkwise: Decide, whether you want to write chunkwise, which will reduce
                               memory usage but will increase vastly the amount of time needed.

        The sequence object is sampled by call subsequently the sampling routine for the
        PulseBlockEnsemble objects and passing if needed the rotating frame option.

        Only those PulseBlockEnsemble object where sampled that are different! These can be
        directly obtained from the internal attribute different_ensembles_dict of a PulseSequence.

        Right now two 'simple' methods of sampling where implemented, which reuse the sample
        function for the Pulse_Block_Ensembles. One, which samples by preserving the phase (i.e.
        staying in the rotating frame) and the other which samples without keep a phase
        relationship between the different entries of the PulseSequence object.

        More sophisticated sequence sampling method can be implemented here.
        """
        # lock module
        if self.getState() == 'idle':
            self.lock()
        else:
            self.log.error('Cannot sample sequence "{0}" because the sequence generator logic is '
                           'still busy (locked).\nFunction call ignored.'.format(sequence_name))
            return
        if write_to_file:
            # get sampled filenames on host PC referring to the same ensemble
            filename_list = [f for f in os.listdir(self.sequence_dir) if
                             f.startswith(sequence_name + '.seq')]
            # delete all filenames in the list
            for file in filename_list:
                os.remove(os.path.join(self.sequence_dir, file))

            if len(filename_list) != 0:
                self.log.warning('Found old sequence for name "{0}". Files deleted before '
                                 'sampling: {1}'.format(sequence_name, filename_list))

        start_time = time.time()
        # get ensemble
        sequence_obj = self.saved_pulse_sequences[sequence_name]
        sequence_param_dict_list = []

        # Here all the sampled ensembles with their result file name will be locally stored:
        sampled_ensembles = OrderedDict()

        # if all the Pulse_Block_Ensembles should be in the rotating frame, then each ensemble
        # will be created in general with a different offset_bin. Therefore, in order to keep track
        # of the sampled Pulse_Block_Ensembles one has to introduce a running number as an
        # additional name tag, so keep the sampled files separate.
        if sequence_obj.rotating_frame:
            ensemble_index = 0  # that will indicate the ensemble index
            offset_bin = 0      # that will be used for phase preserving
            for ensemble_obj, seq_param in sequence_obj.ensemble_param_list:
                # to make something like 001
                name_tag = '_' + str(ensemble_index).zfill(3)

                dummy1, \
                dummy2, \
                created_files, \
                offset_bin_return = self.sample_pulse_block_ensemble(ensemble_obj.name,
                                                                     write_to_file,
                                                                     chunkwise,
                                                                     offset_bin=offset_bin,
                                                                     name_tag=name_tag)

                # the temp_dict is a format how the sequence parameter will be saved
                temp_dict = dict()
                temp_dict['name'] = created_files

                # relate the created_files to a name identifier. Maybe this information will be
                # needed later on about that sequence object
                sampled_ensembles[ensemble_obj.name + name_tag] = created_files
                # update the sequence parameter to the temp dict:
                temp_dict.update(seq_param)
                # add the whole dict to the list of dicts, containing information about how to
                # write the sequence properly in the hardware file:
                sequence_param_dict_list.append(temp_dict)

                # for the next run, the returned offset_bin will serve as starting point for
                # phase preserving.
                offset_bin = offset_bin_return
                ensemble_index += 1
        else:
            # if phase prevervation between the sequence entries is not needed, then only the
            # different ensembles will be sampled, since the offset_bin does not matter for them:
            for ensemble_name in sequence_obj.different_ensembles_dict:
                ensemble_obj = self.saved_pulse_block_ensembles[ensemble_name]

                dummy1, \
                dummy2, \
                created_files, \
                offset_bin = self.sample_pulse_block_ensemble(ensemble_name, write_to_file,
                                                              chunkwise, offset_bin=0, name_tag='')

                # contains information about which file(s) was/were created for the specified
                # ensemble:
                sampled_ensembles[ensemble_name] = created_files

            # go now through the sequence list and replace all the entries with the output of the
            # sampled ensemble file:
            for ensemble_obj, seq_param in sequence_obj.ensemble_param_list:

                temp_dict = dict()
                temp_dict['name'] = sampled_ensembles[ensemble_obj.name]
                # update the sequence parameter to the temp dict:
                temp_dict.update(seq_param)

                sequence_param_dict_list.append(temp_dict)

        # FIXME: That is most propably not a good idea!!! But let's see whether that will work out
        #        and whether it will be necessary (for the upload method it is!)

        sequence_obj.sampled_ensembles = sampled_ensembles
        # save the current object, since it has now a different attribute:
        self.save_sequence(sequence_name, sequence_obj)

        # pass the whole information to the sequence creation method:
        self._write_to_file[self.sequence_format](sequence_name, sequence_param_dict_list)

        self.log.info('Time needed for sampling and writing Pulse Sequence to file as a whole: '
                      '{0} sec.'.format(int(np.rint(time.time() - start_time))))
        self.sigSampleSequenceComplete.emit(sequence_name)
        # unlock module
        self.unlock()
        return

    #---------------------------------------------------------------------------
    #                    END sequence/block sampling
    #---------------------------------------------------------------------------