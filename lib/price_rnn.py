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

DATA_PROVIDER = 'gemini'
PRED_PAIR = 'BTCUSD'
PRED_PERIOD = '1min'
FILE_FILTER = f'{DATA_PROVIDER}_{PRED_PAIR}_*{PRED_PERIOD}.csv'
WINDOW_LEN = 5 # price data window
FORECAST_LEN = 3 # how many data points in future to predict
EPOCHS = 10
BATCH_SIZE = 64
NAME = f'{PRED_PAIR}-{WINDOW_LEN}-SEQ-{FORECAST_LEN}-PRED-{int(time.time())}'
COL_NAMES = ['time', 'date', 'symbol', 'open', 'high', 'low', 'close', 'volume']

def classify(current, future):
    span = float(future) - float(current)
    if 0.0 <= span <= float("inf"):
        return 1
    else:
        return 0

# normalize, scale, balance
def preprocess_df(df):
    df = df.drop('future', 1)
    for col in df.columns:
        if col != 'target':
            # model output is next price normalised to 10th previous closing price
            # LSTM_training_outputs = (training_set['eth_Close'][window_len:].values/training_set['eth_Close'][:-window_len].values)-1
            # df[col] = df[col].pct_change(fill_method ='bfill') # normalizes data
            # df.replace([np.inf, -np.inf], np.nan, inplace=True)
            # df.dropna(inplace=True)
            values = df[col].values
            min_val = min(values)
            max_val = np.nanmax(values)
            df[col] = (values - min_val) / ( max_val - min_val ) # normalize to [0,1]


    df.dropna(inplace=True)

    seq_data = []
    # sliding window cache - old values drop off
    prev_days = deque(maxlen=WINDOW_LEN)

    for i in df.values:
        prev_days.append([n for n in i[:-1]])
        if len(prev_days) == WINDOW_LEN:
            seq_data.append([np.array(prev_days), i[-1]])

    random.shuffle(seq_data)

    # balance the data
    buys, sells = [], []
    for seq, target in seq_data:
        if target == 0:
            sells.append([seq, target])
        elif target == 1:
            buys.append([seq, target])

    random.shuffle(buys)
    random.shuffle(sells)

    lower = min(len(buys), len(sells))
    buys, sells, seq_data = buys[:lower], sells[:lower], buys + sells

    # all buys or all sells skews data, so shuffle
    random.shuffle(seq_data)
    x, y = [], []
    for seq, target in seq_data:
        x.append(seq)
        y.append(target)
    return np.array(x), y


main_df = pd.DataFrame()
years = ['2017', '2018', '2019']

for path, dirlist, filelist in os.walk('data'):
    for year, filename in zip(years, fnmatch.filter(filelist, FILE_FILTER)):
        # if not year == '2019':
        #     continue
        print('LOADING FILE FOR YEAR: ', year)
        file = os.path.join(path, filename)
        df = pd.read_csv(f'{file}', skiprows=184400, names=COL_NAMES)
        df.rename(columns={'close': f'{PRED_PAIR}_close', 'volume': f'{PRED_PAIR}_volume'}, inplace=True)
        df.set_index('time', inplace=True)

        df = df[[f'{PRED_PAIR}_close', f'{PRED_PAIR}_volume']]
        if len(main_df) == 0:
            main_df = df
        else:
            main_df = main_df.append(df)


# add to dataframe
# import pdb; pdb.set_trace()

main_df['future'] = main_df[f'{PRED_PAIR}_close'].shift(-FORECAST_LEN)
main_df['target'] = list(map(classify, main_df[f'{PRED_PAIR}_close'], main_df['future']))

times = sorted(main_df.index.values)
last_5pct = times[-int(0.05*len(times))]

# split data
validation_main_df = main_df[(main_df.index >= last_5pct)]
main_df = main_df[(main_df.index < last_5pct)]

train_x, train_y = preprocess_df(main_df)
validation_x, validation_y = preprocess_df(validation_main_df)
# import pdb; pdb.set_trace()

# shows balance
print(f'train data: {len(train_x)} validation: {len(validation_x)}')
print(f'Dont buys: {train_y.count(0)} buys: {train_y.count(1)}')
print(f'VALIDATION Dont buys: {validation_y.count(0)} VALIDATION buys: {validation_y.count(1)}')

model = Sequential()
model.add(LSTM(128, input_shape=(train_x.shape[1:]), return_sequences=True))
model.add(Dropout(0.2))
model.add(BatchNormalization())

model.add(LSTM(128, input_shape=(train_x.shape[1:]), return_sequences=True))
model.add(Dropout(0.2))
model.add(BatchNormalization())

model.add(LSTM(128, input_shape=(train_x.shape[1:])))
model.add(Dropout(0.2))
model.add(BatchNormalization())

model.add(Dense(128, activation='tanh'))
model.add(Dropout(0.2))
model.add(BatchNormalization())

model.add(Dense(32, activation='relu'))
model.add(Dropout(0.2))

model.add(Dense(2, activation="softmax"))

opt = tf.keras.optimizers.Adam(lr=0.001, decay=1e-6)

model.compile(loss='sparse_categorical_crossentropy',
              optimizer=opt,
              metrics=['accuracy'])

tensorboard = TensorBoard(log_dir=f'logs/{NAME}')

# unique filename to include epoch and validation accuracy for that epoch
if not os.path.exists('models'):
    os.makedirs('models')

filepath = "RNN_Final-{epoch:02d}-{val_acc:.3f}"
checkpoint = ModelCheckpoint("models/{}.model".format(filepath,
                                                      monitor='val_acc',
                                                      verbose=1,
                                                      save_best_only=True,
                                                      mode='max')) #saves only the best ones

history = model.fit(
    train_x, train_y,
    batch_size=BATCH_SIZE,
    epochs=EPOCHS,
    validation_data=(validation_x, validation_y),
    callbacks=[tensorboard, checkpoint])

model.evaluate(validation_x, validation_y)
