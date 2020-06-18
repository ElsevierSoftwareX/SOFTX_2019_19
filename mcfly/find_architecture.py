#
# mcfly
#
# Copyright 2020 Netherlands eScience Center
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""
 Summary:
 This module provides the main functionality of mcfly: searching for an
 optimal model architecture. The work flow is as follows:
 Function generate_models from modelgen.py generates and compiles models.
 Function train_models_on_samples trains those models.
 Function find_best_architecture is wrapper function that combines
 these steps.
 Example function calls can be found in the tutorial notebook
 (https://github.com/NLeSC/mcfly-tutorial)
"""
import json
import os
import warnings

import numpy as np
from sklearn import neighbors, metrics as sklearnmetrics
from tensorflow.keras import metrics
from tensorflow.keras.callbacks import EarlyStopping

from . import modelgen


def train_models_on_samples(X_train, y_train, X_val, y_val, models,
                            nr_epochs=5, subset_size=100, verbose=True, outputfile=None,
                            model_path=None, early_stopping_patience='auto',
                            batch_size=20, metric='accuracy', class_weight=None):
    """
    Given a list of compiled models, this function trains
    them all on a subset of the train data. If the given size of the subset is
    smaller then the size of the data, the complete data set is used.

    Parameters
    ----------
    X_train : numpy array of shape (num_samples, num_timesteps, num_channels)
        The input dataset for training
    y_train : numpy array of shape (num_samples, num_classes)
        The output classes for the train data, in binary format
    X_val : numpy array of shape (num_samples_val, num_timesteps, num_channels)
        The input dataset for validation
    y_val : numpy array of shape (num_samples_val, num_classes)
        The output classes for the validation data, in binary format
    models : list of model, params, modeltypes
        List of keras models to train
    nr_epochs : int, optional
        nr of epochs to use for training one model
    subset_size :
        The number of samples used from the complete train set. If set to 'None'
        use the entire dataset. Default is 100, but should be adjusted depending 
        on the type ans size of the dataset.
    verbose : bool, optional
        flag for displaying verbose output
    outputfile: str, optional
        Filename to store the model training results
    model_path : str, optional
        Directory to store the models as HDF5 files
    early_stopping_patience: str, int
        Unless 'None' early Stopping is used for the model training. Set to integer
        to define how many epochs without improvement to wait for before stopping.
        Default is 'auto' in which case the patience will be set to number of epochs/10 
        (and not bigger than 5).
    batch_size : int
        nr of samples per batch
    metric : str
        metric to store in the history object
    class_weight: dict, optional
        Dictionary containing class weights (example: {0: 0.5, 1: 2.})

    Returns
    ----------
    histories : list of Keras History objects
        train histories for all models
    val_metrics : list of floats
        validation accuraracies of the models
    val_losses : list of floats
        validation losses of the models
    """
    
    if subset_size is None:
        subset_size = -1
    if subset_size != -1:
        print("Generated models will be trained on subset of the data (subset size: {})."
              .format(str(subset_size)))

    X_train_sub = X_train[:subset_size, :, :]
    y_train_sub = y_train[:subset_size, :]

    metric_name = _get_metric_name(metric)

    histories = []
    val_metrics = []
    val_losses = []
    for i, (model, params, model_types) in enumerate(models):
        if verbose:
            print('Training model %d' % i, model_types)
        model_metrics = [_get_metric_name(metric) for metric in model.metrics_names]
        if metric_name not in model_metrics:
            raise ValueError('Invalid metric: "{}" is not among the metrics the models was compiled with ({}).'
                             .format(metric_name, model_metrics))
        if early_stopping_patience is not None:
            if early_stopping_patience == 'auto':
                callbacks = [EarlyStopping(monitor='val_loss', patience=min(nr_epochs//10, 5), verbose=verbose, mode='auto')]
            else:
                callbacks = [EarlyStopping(monitor='val_loss', patience=early_stopping_patience, verbose=verbose, mode='auto')]
        else:
            callbacks = []
        history = model.fit(X_train_sub, y_train_sub,
                            epochs=nr_epochs, batch_size=batch_size,
                            # see comment on subsize_set
                            validation_data=(X_val, y_val),
                            verbose=verbose,
                            callbacks=callbacks,
                            class_weight=class_weight)
        histories.append(history)

        val_metrics.append(_get_from_history('val_' + metric_name, history.history)[-1])
        val_losses.append(_get_from_history('val_loss', history.history)[-1])
        if outputfile is not None:
            store_train_hist_as_json(params, model_types, history.history,
                                     outputfile, metric_name)
        if model_path is not None:
            model.save(os.path.join(model_path, 'model_{}.h5'.format(i)))

    return histories, val_metrics, val_losses


def _get_from_history(metric_name, history_history):
    """Gets the metric from the history object. Tries to solve inconsistencies in abbreviation of accuracy between
    Tensorflow/Keras versions. """
    if metric_name == 'val_accuracy':
        return _get_either_from_history('val_accuracy', 'val_acc', history_history)
    elif metric_name == 'accuracy':
        return _get_either_from_history('accuracy', 'acc', history_history)
    else:
        return history_history[metric_name]


def _get_either_from_history(option1, option2, history_history):
    try:
        return history_history[option1]
    except KeyError:
        try:
            return history_history[option2]
        except KeyError:
            raise KeyError('No {} or {} in history.'.format(option1, option2))


def store_train_hist_as_json(params, model_type, history, outputfile, metric_name='accuracy'):
    """
    This function stores the model parameters, the loss and accuracy history
    of one model in a JSON file. It appends the model information to the
    existing models in the file.

    Parameters
    ----------
    params : dict
        parameters for one model
    model_type : Keras model object
        Keras model object for one model
    history : dict
        training history from one model
    outputfile : str
        path where the json file needs to be stored
    metric_name : str, optional
        name of metric from history to store
    """
    jsondata = params.copy()
    jsondata['train_metric'] = _get_from_history(metric_name, history)
    jsondata['train_loss'] = _get_from_history('loss', history)
    jsondata['val_metric'] = _get_from_history('val_' + metric_name, history)
    jsondata['val_loss'] = _get_from_history('val_loss', history)
    jsondata['modeltype'] = model_type
    jsondata['metric'] = metric_name
    for k in jsondata.keys():
        if isinstance(jsondata[k], np.ndarray) or isinstance(jsondata[k], list):
            jsondata[k] = [_cast_to_primitive_type(element) for element in jsondata[k]]
    if os.path.isfile(outputfile):
        with open(outputfile, 'r') as outfile:
            previousdata = json.load(outfile)
    else:
        previousdata = []
    previousdata.append(jsondata)
    with open(outputfile, 'w') as outfile:
        json.dump(previousdata, outfile, sort_keys=True,
                  indent=4, ensure_ascii=False)


def _cast_to_primitive_type(obj):
    if isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    else:
        return obj


def find_best_architecture(X_train, y_train, X_val, y_val, verbose=True,
                           number_of_models=5, nr_epochs=5, subset_size=100,
                           outputpath=None, model_path=None, metric='accuracy',
                           class_weight=None,
                           **kwargs):
    """
    Tries out a number of models on a subsample of the data,
    and outputs the best found architecture and hyperparameters.

    Parameters
    ----------
    X_train : numpy array
        The input dataset for training of shape
        (num_samples, num_timesteps, num_channels)
    y_train : numpy array
        The output classes for the train data, in binary format of shape
        (num_samples, num_classes)
    X_val : numpy array
        The input dataset for validation of shape
        (num_samples_val, num_timesteps, num_channels)
    y_val : numpy array
        The output classes for the validation data, in binary format of shape
        (num_samples_val, num_classes)
    verbose : bool, optional
        flag for displaying verbose output
    number_of_models : int, optiona
        The number of models to generate and test
    nr_epochs : int, optional
        The number of epochs that each model is trained
    subset_size : int, optional
        The size of the subset of the data that is used for finding
        the optimal architecture. Default is 100.
    outputpath : str, optional
        File location to store the model results
    model_path: str, optional
        Directory to save the models as HDF5 files
    class_weight: dict, optional
        Dictionary containing class weights (example: {0: 0.5, 1: 2.})
    metric: str, optional
        metric that is used to evaluate the model on the validation set.
        See https://keras.io/metrics/ for possible metrics
    **kwargs: key-value parameters
        parameters for generating the models
        (see docstring for modelgen.generate_models)

    Returns
    ----------
    best_model : Keras model
        Best performing model, already trained on a small sample data set.
    best_params : dict
        Dictionary containing the hyperparameters for the best model
    best_model_type : str
        Type of the best model
    knn_acc : float
        accuaracy for kNN prediction on validation set
    """
    models = modelgen.generate_models(X_train.shape, y_train.shape[1],
                                      number_of_models=number_of_models,
                                      metrics=[metric],
                                      **kwargs)
    histories, val_accuracies, val_losses = train_models_on_samples(X_train,
                                                                    y_train,
                                                                    X_val,
                                                                    y_val,
                                                                    models,
                                                                    nr_epochs,
                                                                    subset_size=subset_size,
                                                                    verbose=verbose,
                                                                    outputfile=outputpath,
                                                                    model_path=model_path,
                                                                    metric=metric,
                                                                    class_weight=class_weight)
    best_model_index = np.argmax(val_accuracies)
    best_model, best_params, best_model_type = models[best_model_index]
    knn_acc = kNN_accuracy(
        X_train[:subset_size, :, :], y_train[:subset_size, :], X_val, y_val)
    if verbose:
        print('Best model: model ', best_model_index)
        print('Model type: ', best_model_type)
        print('Hyperparameters: ', best_params)
        print(str(metric) + ' on validation set: ',
              val_accuracies[best_model_index])
        print('Accuracy of kNN on validation set', knn_acc)

    if val_accuracies[best_model_index] < knn_acc:
        warnings.warn('Best model not better than kNN: ' +
                      str(val_accuracies[best_model_index]) + ' vs  ' +
                      str(knn_acc)
                      )
    return best_model, best_params, best_model_type, knn_acc


def _get_metric_name(name):
    """
    Gives the keras name for a metric

    Parameters
    ----------
    name : str
        original name of the metric
    Returns
    -------

    """
    if name == 'acc' or name == 'accuracy':
        return 'accuracy'
    try:
        metric_fn = metrics.get(name)
        return metric_fn.__name__
    except:
        pass
    return name


def kNN_accuracy(X_train, y_train, X_val, y_val, k=1):
    """
    Performs k-Neigherst Neighbors and returns the accuracy score.

    Parameters
    ----------
    X_train : numpy array
        Train set of shape (num_samples, num_timesteps, num_channels)
    y_train : numpy array
        Class labels for train set
    X_val : numpy array
        Validation set of shape (num_samples, num_timesteps, num_channels)
    y_val : numpy array
        Class labels for validation set
    k : int
        number of neighbors to use for classifying

    Returns
    -------
    accuracy: float
        accuracy score on the validation set
    """
    num_samples, num_timesteps, num_channels = X_train.shape
    clf = neighbors.KNeighborsClassifier(k)
    clf.fit(
        X_train.reshape(
            num_samples,
            num_timesteps *
            num_channels),
        y_train)
    num_samples, num_timesteps, num_channels = X_val.shape
    val_predict = clf.predict(
        X_val.reshape(num_samples,
                      num_timesteps * num_channels))
    return sklearnmetrics.accuracy_score(val_predict, y_val)
