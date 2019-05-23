"""
An deep RNN model for price sequence data
"""
import os
import re
import fnmatch
from itertools import zip_longest
import pandas as pd
from sklearn import preprocessing
from collections import deque
import random
import numpy as np
import time
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Dense, Dropout, LSTM, CuDNNLSTM, BatchNormalization
from tensorflow.keras.callbacks import TensorBoard, ModelCheckpoint
from sklearn.preprocessing import MinMaxScaler
from tenserflow.keras import regularizers


class PriceRNN:
    def __init__(
        self,
        pair="BTCUSD",  # UPPERCASE
        period="1min",
        window_len=60,
        forecast_len=3,
        years=["2015", "2016", "2017", "2018", "2019"],
        epochs=1,
        testpct=0.40,
        batch_size=64,
        hidden_node_sizes=[128] * 4,
        data_provider="gemini",
        data_dir="data",
        skip_rows=2,
    ):
        self.data_provider = data_provider
        self.data_dir = data_dir
        self.pair = pair
        self.period = period
        self.file_filter = f"{data_provider}_{pair}_*{period}.csv"
        self.window_len = window_len  # price data window
        self.forecast_len = forecast_len  # how many data points in future to predict
        self.years = years
        self.epochs = epochs
        self.testpct = testpct
        self.batch_size = batch_size
        self.hidden_node_sizes = hidden_node_sizes
        self.name = f"{pair}-{window_len}-window-{forecast_len}-pred-{int(time.time())}"
        self.skip_rows = skip_rows
        self.col_names = [
            "time",
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        self.file_filter = f"{data_provider}_{pair}_*{period}.csv"

    def classify(self, current, future):
        span = float(future) - float(current)
        if 0.0 < span <= float("inf"):
            return 1
        else:
            return 0

    def normalize_df(self, df):
        print("NORMALIZING DATA:\n", df.sample(10))
        # normalize df.columns ({PAIR}_close, {PAIR}_volume)
        for col in df.columns:
            if col != "target":
                # start simple, scale to interval [0,1]
                df[col] = (df[col] - df[col].mean()) / (df[col].max() - df[col].min())
        return df

    def arrange_df(self, df):
        print("ARRANGING NORMALIZED DATA:\n", df.sample(10))
        # arrange data into seq -> target pairs for training to see how
        # WINDOW_LEN 'lookback' period effects prediction accuracy
        seq_data = []
        # sliding window cache - old values drop off
        prev_days = deque(maxlen=self.window_len)
        for i in df.values:
            prev_days.append([n for n in i[:-1]])  # exclude target (i[:-1])
            if len(prev_days) == self.window_len:
                seq_data.append([np.array(prev_days), i[-1]])
        random.shuffle(seq_data)  # prevent skew
        return seq_data

    def balance(self, seq_data):
        print("BALANCING DATA:\n", seq_data[0][0][0:2])
        # balance the data
        buys, sells = [], []
        for seq, target in seq_data:
            if target == 0:
                sells.append([seq, target])
            elif target == 1:
                buys.append([seq, target])

        # randomize
        random.shuffle(buys)
        random.shuffle(sells)
        # balance out the distribution of buys and sells
        lower = min(len(buys), len(sells))
        buys = buys[:lower]
        sells = sells[:lower]
        seq_data = buys + sells
        return seq_data

    def split_sequences(self, seq_data):
        print("SPLITTING DATA:\n", seq_data[0][0][0:2])
        # split data into train, test sets
        # to prevent buys or sells from skewing data, randomize
        random.shuffle(seq_data)
        x, y = [], []
        for window_seq, target in seq_data:
            x.append(window_seq)
            y.append(target)

        print("TRAINING DATA SAMPLE:\n", x[0][0][0:2][:])
        print("TEST DATA SAMPLE:\n", len(y))
        return np.array(x), y

    # normalize, arrange, balance
    def preprocess_df(self, df):
        df = df.drop("future", 1)
        df = self.normalize_df(df)
        seq_data = self.arrange_df(df)
        seq_data = self.balance(seq_data)
        x, y = self.split_sequences(seq_data)
        return x, y

    def extract_data(self):
        main_df = pd.DataFrame()
        for path, dirlist, filelist in os.walk(self.data_dir):
            for year, filename in zip(
                self.years, fnmatch.filter(filelist, self.file_filter)
            ):
                for allowed_year in self.years:
                    if not allowed_year == year:
                        continue
                print("LOADING FILE FOR YEAR: ", year)
                file = os.path.join(path, filename)
                df = pd.read_csv(
                    f"{file}", skiprows=self.skip_rows, names=self.col_names
                )

                df.rename(
                    columns={
                        "close": f"{self.pair}_close",
                        "volume": f"{self.pair}_volume",
                    },
                    inplace=True,
                )
                df.set_index("time", inplace=True)

                # the features we care about
                df = df[[f"{self.pair}_close", f"{self.pair}_volume"]]
                if len(main_df) == 0:
                    main_df = df
                else:
                    main_df = main_df.append(df)
        return main_df

    def transform_df(self, df):
        # add a future price column shifted in relation to close
        df["future"] = df[f"{self.pair}_close"].shift(-self.forecast_len)
        # classify and add target ground truth column
        df["target"] = list(map(self.classify, df[f"{self.pair}_close"], df["future"]))
        return df

    def preprocess_and_split(self, df):
        times = sorted(df.index.values)
        testpct = times[-int(self.testpct * len(times))]

        # SPLIT DATA INTO (1-testpct)% TRAIN, (testpct)% VALIDATE
        test_df = df[(df.index >= testpct)]
        df = df[(df.index < testpct)]

        x_train, y_train = self.preprocess_df(df)
        x_test, y_test = self.preprocess_df(test_df)
        return x_train, y_train, x_test, y_test

    def run(self):
        main_df = self.extract_data()
        main_df = self.transform_df(main_df)
        x_train, y_train, x_test, y_test = self.preprocess_and_split(main_df)

        # shows balance
        print(f"train data: {len(x_train)}, validation data: {len(x_test)}")
        print(f"TRAIN do not buys: {y_train.count(0)} TRAIN buys: {y_train.count(1)}")
        print(
            f"VALIDATION Do not buys: {y_test.count(0)} VALIDATION buys: {y_test.count(1)}"
        )

        model = Sequential()
        model.add(
            CuDNNLSTM(
                self.hidden_node_sizes[0],
                input_shape=(x_train.shape[1:]),
                return_sequences=True,
            )
        )
        model.add(Dropout(0.4))
        model.add(BatchNormalization())

        model.add(
            CuDNNLSTM(
                self.hidden_node_sizes[1],
                input_shape=(x_train.shape[1:]),
                return_sequences=True,
            )
        )
        model.add(Dropout(0.4))
        model.add(BatchNormalization())

        model.add(
            CuDNNLSTM(
                self.hidden_node_sizes[2],
                input_shape=(x_train.shape[1:]),
                return_sequences=True,
            )
        )
        model.add(Dropout(0.4))
        model.add(BatchNormalization())

        model.add(Dense(32, activation="relu"))

        model.add(Dense(2, activation="softmax"))

        opt = tf.keras.optimizers.Adam(lr=0.001, decay=1e-6)

        model.compile(
            loss="sparse_categorical_crossentropy", optimizer=opt, metrics=["accuracy"]
        )

        if not os.path.exists("logs"):
            os.makedirs("logs")
        tensorboard = TensorBoard(log_dir=f"logs/{self.name}")

        if not os.path.exists("models"):
            os.makedirs("models")

        # unique filename to include epoch and validation accuracy for that epoch
        filepath = "RNN_Final-{epoch:02d}-{val_acc:.3f}"
        checkpoint = ModelCheckpoint(
            "models/{}.model".format(
                filepath, monitor="val_acc", verbose=1, save_best_only=True, mode="max"
            )
        )  # saves only the best ones

        history = model.fit(
            x_train,
            y_train,
            batch_size=self.batch_size,
            epochs=self.epochs,
            validation_data=(x_test, y_test),
            callbacks=[tensorboard, checkpoint],
        )

        print(model.evaluate(x_test, y_test))


# TODO: stochastic grid search hyperparam optimization
lens = [(60, 15), (120, 15), (180, 15), (240, 15), (300, 15), (360, 15)]
for wlen, flen in lens:
    wlen = int(wlen)
    flen = int(flen)
    print("RUNNING MODEL: ")
    print("\twindow length: ", wlen)
    print("\tforecast length: ", flen)
    PriceRNN(
        pair="BTCUSD",
        period="1min",
        window_len=wlen,
        forecast_len=flen,
        years=["2019"],
        epochs=10,
        data_dir="/crypto_data",
        skip_rows=2,
    ).run()
