import time
import os
import re
import csv
import sys
import uuid
import joblib
import getopt
import pickle
from datetime import date
from collections import defaultdict
import numpy as np
import pandas as pd
from sklearn import svm
from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor
from sklearn.metrics import mean_squared_error
from sklearn.preprocessing import StandardScaler, RobustScaler
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from logger import update_predict_log, update_train_log
from cslib import fetch_ts, engineer_features

# model specific variables (iterate the version and note with each change)
MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', "models")
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cs-train')
MODEL_VERSION = 0.1
MODEL_VERSION_NOTE = "supervised learing model for time-series"
DEFAULT_MODEL = RandomForestRegressor()
DEFAULT_PARAM_GRID = {
    'rf__criterion': ['mse', 'mae'],
    'rf__n_estimators': [10, 15, 20, 25]
}
DEFAULT_SCALER = StandardScaler()


def _model_train(prefix, df, tag, test=False, model=DEFAULT_MODEL,
                 model_param_grid=DEFAULT_PARAM_GRID, scaler=DEFAULT_SCALER):
    """
    example funtion to train model

    The 'test' flag when set to 'True':
        (1) subsets the data and serializes a test version
        (2) specifies that the use of the 'test' log file 

    """
    # start timer for runtime
    time_start = time.time()

    X, y, dates = engineer_features(df)

    if test:
        n_samples = int(np.round(0.3 * X.shape[0]))
        subset_indices = np.random.choice(np.arange(X.shape[0]), n_samples,
                                          replace=False).astype(int)
        mask = np.in1d(np.arange(y.size), subset_indices)
        y = y[mask]
        X = X[mask]
        dates = dates[mask]

    # Perform a train-test split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25,
                                                        shuffle=True, random_state=42)

    pipe_rf = Pipeline(steps=[('scaler', scaler),
                              ('rf', model)])

    grid = GridSearchCV(pipe_rf, param_grid=model_param_grid, cv=5, n_jobs=-1)
    grid.fit(X_train, y_train)
    y_pred = grid.predict(X_test)
    eval_rmse = round(np.sqrt(mean_squared_error(y_test, y_pred)))

    # retrain using all data
    grid.fit(X, y)
    model_name = re.sub("\.", "_", str(MODEL_VERSION))

    if test:
        saved_model = os.path.join(MODEL_DIR,
                                   "test-{}-{}.joblib".format(tag, model_name))
        print("... saving test version of model: {}".format(saved_model))
    else:
        saved_model = os.path.join(MODEL_DIR,
                                   "{}-{}-{}.joblib".format(prefix, tag, model_name))
        print("... saving model: {}".format(saved_model))
        data_file = os.path.join(
            MODEL_DIR, '{}-{}-{}-train.pickle'.format(prefix, tag, model_name))
        with open(data_file, 'wb') as tmp:
            pickle.dump({'y': y, 'X': X}, tmp)
        print("... saving latest data")

    joblib.dump(grid, saved_model)

    m, s = divmod(time.time()-time_start, 60)
    h, m = divmod(m, 60)
    runtime = "%03d:%02d:%02d" % (h, m, s)

    # update log
    update_train_log(tag, (str(dates[0]), str(dates[-1])), {'rmse': eval_rmse}, runtime,
                     MODEL_VERSION, MODEL_VERSION_NOTE, test=test)


def model_train(prefix='sl', data_dir=DATA_DIR, test=False, countries=False,
                model=DEFAULT_MODEL, model_param_grid=DEFAULT_PARAM_GRID, scaler=DEFAULT_SCALER):
    """
    funtion to train model given a df    
    'mode' -  can be used to subset data essentially simulating a train
    """

    if not os.path.isdir(MODEL_DIR):
        os.mkdir(MODEL_DIR)

    if test:
        print("... test flag on")
        print("...... subseting data")
        print("...... subseting countries")

    # fetch time-series formatted data
    ts_data = fetch_ts(data_dir)

    # train a different model for each data sets
    for country, df in ts_data.items():
        # only train model for all and uk in test mode
        if test and country not in ['all', 'united_kingdom']:
            continue
        # only train model for country in countries
        if countries and not (country in countries):
            continue
        _model_train(prefix, df, country, test=test, model=model,
                     model_param_grid=model_param_grid, scaler=scaler)


def model_load(prefix='sl', data_dir=DATA_DIR, training=True, countries=False):
    """
    example funtion to load model

    The prefix allows the loading of different models
    """

    if not data_dir:
        data_dir = os.path.join("..", "data", "cs-train")

    models = [f for f in os.listdir(MODEL_DIR) if re.search(prefix, f)]

    if len(models) == 0:
        raise Exception(
            "Models with prefix '{}' cannot be found did you train?".format(prefix))

    all_models = {}
    for model in models:
        if not countries or re.split("-", model)[1] in countries:
            all_models[re.split("-", model)[1]
                       ] = joblib.load(os.path.join(MODEL_DIR, model))

    # load data
    ts_data = fetch_ts(data_dir)
    all_data = {}
    for country, df in ts_data.items():
        if countries and not country in countries:
            continue
        X, y, dates = engineer_features(df, training=training)
        dates = np.array([str(d) for d in dates])
        all_data[country] = {"X": X, "y": y, "dates": dates}

    return(all_data, all_models)


def nearest(items, pivot):
    return min(items, key=lambda x: abs(date.fromisoformat(x) - pivot))


def model_predict(country, year, month, day, all_models=None, test=False, prefix='sl'):
    """
    example funtion to predict from model
    """

    # start timer for runtime
    time_start = time.time()

    # load model if needed
    if not all_models:
        all_data, all_models = model_load(training=False)

    # input checks
    if country not in all_models.keys():
        raise Exception(
            "ERROR (model_predict) - model for country '{}' could not be found".format(country))

    for d in [year, month, day]:
        if re.search("\D", d):
            raise Exception(
                "ERROR (model_predict) - invalid year, month or day")

    # load data
    model = all_models[country]
    data = all_data[country]

    # check date
    target_date = "{}-{}-{}".format(year,
                                    str(month).zfill(2), str(day).zfill(2))
    print(target_date)

    if target_date not in data['dates']:
        print("ERROR (model_predict) - date {} not in range {}-{}".format(target_date,
                                                                          data['dates'][0],
                                                                          data['dates'][-1]))
        target_date = nearest(data['dates'], date.fromisoformat(target_date))
        print("Nearest target date is {}".format(target_date))

    date_indx = np.where(data['dates'] == target_date)[0][0]
    query = data['X'].iloc[[date_indx]]

    # sainty check
    if data['dates'].shape[0] != data['X'].shape[0]:
        raise Exception("ERROR (model_predict) - dimensions mismatch")

    # make prediction and gather data for log entry
    y_pred = model.predict(query)
    y_proba = []
    if 'predict_proba' in dir(model) and 'probability' in dir(model):
        if model.probability == True:
            y_proba = model.predict_proba(query)

    m, s = divmod(time.time()-time_start, 60)
    h, m = divmod(m, 60)
    runtime = "%03d:%02d:%02d" % (h, m, s)

    # update predict log
    update_predict_log(country, y_pred, y_proba, target_date,
                       runtime, MODEL_VERSION, test=test)

    return({'y_pred': y_pred, 'y_proba': y_proba})


def get_preprocessor(scaler=DEFAULT_SCALER):
    """
    return the preprocessing pipeline
    """
    # preprocessing pipeline
    numeric_features = ['previous_7', 'previous_14', 'previous_28', 'previous_70', 'previous_year',
                        'recent_invoices', 'recent_views']
    numeric_transformer = Pipeline(steps=[('scaler', scaler)])

    preprocessor = ColumnTransformer(
        transformers=[('num', numeric_transformer, numeric_features)])
    return (preprocessor)


if __name__ == "__main__":
    from argparse import ArgumentParser
    ap = ArgumentParser()
    ap.add_argument('-t', '--training', choices=['test, prod'],
                    help='Train either a test or production model. Omitting this implies loading an already-trained model')
    ap.add_argument('-m', '--model', choices=[
                    'rf', 'et'], help='(rf) RandomForestRegressor or (et) ExtraTreesRegressor (default)', default='et')
    ap.add_argument('-s', '--scaler', choices=[
                    'ss', 'rs'], help='(ss) StandardScaler or (rs) RobustScaler (default)', default='rs')
    ap.add_argument('-c', '--countries', required=True,
                    help='Comma separated list of countries to predict revenue')
    args = ap.parse_args()
    countries = args['countries'].split(',')
    train = args['training']
    model = RandomForestRegressor(
    ) if args['model'] == 'rf' else ExtraTreesRegressor
    scaler = StandardScaler() if args['scaler'] == 'ss' else RobustScaler()
    """
    basic test procedure for model.py
    """
    if train == 'test':
        # train the model - Test
        print("TRAINING MODELS - TEST")
        model_train(data_dir=DATA_DIR, test=True, model=model,
                    countries=countries, scaler=scaler)
    elif train == 'prod':
        # train the model
        print("TRAINING MODELS")
        model_train(data_dir=DATA_DIR, test=False, model=model,
                    countries=countries, scaler=scaler)
    else:
        # load the model
        print("LOADING MODELS")
        all_data, all_models = model_load(training=False, countries=countries)
        print("... models loaded: ", ",".join(all_models.keys()))

    # test predict
    year = '2019'
    month = '06'
    day = '05'
    for country in countries:
        result = model_predict(country, year, month, day)
        print("Predicted revenue for {} is {}".format(
            country, result['y_pred'][0]))
