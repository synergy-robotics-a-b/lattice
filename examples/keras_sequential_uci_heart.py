# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""Example usage of TFL within Keras models.

This example builds and trains a calibrated lattice model for the UCI heart
dataset.

"Calibrated lattice" is a commonly used architecture for datasets where number
of input features does not exceed ~15.

"Calibrated lattice" assumes every feature being transformed by PWLCalibration
or CategoricalCalibration layers before nonlineary fusing result of calibration
within a lattice layer.

Generally when you manually combine TFL layers you should keep track of:
1) Ensuring that inputs to TFL layers are within expected range.
  - Input range for PWLCalibration layer is defined by smallest and largest of
    provided keypoints.
  - Input range for Lattice layer is [0.0, lattice_sizes[d] - 1.0] for any
    dimension d.
  TFL layers can constraint their output to be within desired range. Feeding
  output of other layers into TFL layers you might want to ensure that something
  like sigmoid is used to constraint their output range.
2) Properly configure monotonicity. If your calibration layer is monotonic then
  corresponding dimension of lattice layer should also be monotonic.

This example creates a Sequential Keras model and only uses TFL layers. For an
example of functional model construction that also use embedding layers see
keras_functional_uci_heart.py.

In order to see how better generalization can be achieved with a properly
constrained PWLCalibration layer compared to a vanila embedding layer, compare
training and validation losses of this model with one defined in
keras_functional_uci_heart.py


Note that the specifics of layer configurations are for demonstration purposes
and might not result in optimal performance.

Example usage:
keras_sequential_uci_heart
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl import app
from absl import flags

import numpy as np
import pandas as pd

import tensorflow as tf
from tensorflow import keras
import tensorflow_lattice as tfl

FLAGS = flags.FLAGS
flags.DEFINE_integer('num_epochs', 200, 'Number of training epoch.')


def main(_):
  # UCI Statlog (Heart) dataset.
  csv_file = tf.keras.utils.get_file(
      'heart.csv', 'http://storage.googleapis.com/applied-dl/heart.csv')
  training_data_df = pd.read_csv(csv_file).sample(
      frac=1.0, random_state=41).reset_index(drop=True)

  # Feature columns.
  # 0  age
  # 1  sex
  # 2  cp        chest pain type (4 values)
  # 3  trestbps  resting blood pressure
  # 4  chol      serum cholestoral in mg/dl
  # 5  fbs       fasting blood sugar > 120 mg/dl
  # 6  restecg   resting electrocardiographic results (values 0,1,2)
  # 7  thalach   maximum heart rate achieved
  # 8  exang     exercise induced angina
  # 9  oldpeak   ST depression induced by exercise relative to rest
  # 10 slope     the slope of the peak exercise ST segment
  # 11 ca        number of major vessels (0-3) colored by flourosopy
  # 12 thal      3 = normal; 6 = fixed defect; 7 = reversable defect

  # Example slice of training data:
  #     age  sex  cp  trestbps  chol  fbs  restecg  thalach  exang  oldpeak
  # 0   63    1   1       145   233    1        2      150      0      2.3
  # 1   67    1   4       160   286    0        2      108      1      1.5
  # 2   67    1   4       120   229    0        2      129      1      2.6
  # 3   37    1   3       130   250    0        0      187      0      3.5
  # 4   41    0   2       130   204    0        2      172      0      1.4
  # 5   56    1   2       120   236    0        0      178      0      0.8
  # 6   62    0   4       140   268    0        2      160      0      3.6
  # 7   57    0   4       120   354    0        0      163      1      0.6
  # 8   63    1   4       130   254    0        2      147      0      1.4
  # 9   53    1   4       140   203    1        2      155      1      3.1

  # Lattice sizes per dimension for Lattice layer.
  # Lattice layer expects input[i] to be within [0, lattice_sizes[i] - 1.0], so
  # we need to define lattice sizes ahead of calibration layers so we can
  # properly specify output range of calibration layers.
  lattice_sizes = [3, 2, 2, 2, 2, 2, 2]

  # Use ParallelCombination helper layer to group togehter calibration layers
  # which have to be executed in paralel in order to be able to use Sequential
  # model. Alternatively you can use functional API.
  combined_calibrators = tfl.layers.ParallelCombination()

  # Configure calibration layers for every feature:

  # ############### age ###############

  calibrator = tfl.layers.PWLCalibration(
      # Every PWLCalibration layer must have keypoints of piecewise linear
      # function specified. Easiest way to specify them is to uniformly cover
      # entire input range by using numpy.linspace().
      input_keypoints=np.linspace(training_data_df['age'].min(),
                                  training_data_df['age'].max(),
                                  num=5),
      # You need to ensure that input keypoints have same dtype as layer input.
      # You can do it by setting dtype here or by providing keypoints in such
      # format which will be converted to deisred tf.dtype by default.
      dtype=tf.float32,
      # Output range must correspond to expected lattice input range.
      output_min=0.0,
      output_max=lattice_sizes[0] - 1.0,
      monotonicity='increasing')
  combined_calibrators.append(calibrator)

  # ############### sex ###############

  # For boolean features simply specify CategoricalCalibration layer with 2
  # buckets.
  calibrator = tfl.layers.CategoricalCalibration(
      num_buckets=2,
      output_min=0.0,
      output_max=lattice_sizes[1] - 1.0,
      # Initializes all outputs to (output_min + output_max) / 2.0.
      kernel_initializer='constant')
  combined_calibrators.append(calibrator)

  # ############### cp ###############

  calibrator = tfl.layers.PWLCalibration(
      # Here instead of specifying dtype of layer we convert keypoints into
      # np.float32.
      input_keypoints=np.linspace(1, 4, num=4, dtype=np.float32),
      output_min=0.0,
      output_max=lattice_sizes[2] - 1.0,
      monotonicity='increasing',
      # You can specify TFL regularizers as tuple ('regularizer name', l1, l2).
      kernel_regularizer=('hessian', 0.0, 1e-4))
  combined_calibrators.append(calibrator)

  # ############### trestbps ###############

  calibrator = tfl.layers.PWLCalibration(
      # Alternatively to uniform keypoints you might want to use quantiles as
      # keypoints.
      input_keypoints=np.quantile(
          training_data_df['trestbps'], np.linspace(0.0, 1.0, num=5)),
      dtype=tf.float32,
      # Together with quantile keypoints you might want to initialize piecewise
      # linear function to have 'equal_slopes' in order for output of layer
      # after initialization to preserve original distribution.
      kernel_initializer='equal_slopes',
      output_min=0.0,
      output_max=lattice_sizes[3] - 1.0,
      # You might consider clamping extreme inputs of the calibrator to output
      # bounds.
      clamp_min=True,
      clamp_max=True,
      monotonicity='increasing')
  combined_calibrators.append(calibrator)

  # ############### chol ###############

  calibrator = tfl.layers.PWLCalibration(
      # Explicit input keypoint initialization.
      input_keypoints=[126.0, 210.0, 247.0, 286.0, 564.0],
      dtype=tf.float32,
      output_min=0.0,
      output_max=lattice_sizes[4] - 1.0,
      # Monotonicity of calibrator can be 'decreasing'. Note that corresponding
      # lattice dimension must have 'increasing' monotonicity regardless of
      # monotonicity direction of calibrator.
      # Its not some weird configuration hack. Its just how math works :)
      monotonicity='decreasing',
      # Convexity together with decreasing monotonicity result in diminishing
      # return constraint.
      convexity='convex',
      # You can specify list of regularizers. You are not limited to TFL
      # regularizrs. Feel free to use any :)
      kernel_regularizer=[('laplacian', 0.0, 1e-4),
                          keras.regularizers.l1_l2(l1=0.001)])
  combined_calibrators.append(calibrator)

  # ############### fbs ###############

  calibrator = tfl.layers.CategoricalCalibration(
      num_buckets=2,
      output_min=0.0,
      output_max=lattice_sizes[5] - 1.0,
      # For categorical calibration layer monotonicity is specified for pairs
      # of indices of categories. Output for first category in pair will be
      # smaller than output for second category.
      #
      # Don't forget to set monotonicity of corresponding dimension of Lattice
      # layer to 'increasing'.
      monotonicities=[(0, 1)],
      # This initializer is identical to default one('uniform'), but has fixed
      # seed in order to simplify experimentation.
      kernel_initializer=keras.initializers.RandomUniform(
          minval=0.0, maxval=lattice_sizes[5] - 1.0, seed=1))
  combined_calibrators.append(calibrator)

  # ############### restecg ###############

  calibrator = tfl.layers.CategoricalCalibration(
      num_buckets=3,
      output_min=0.0,
      output_max=lattice_sizes[6] - 1.0,
      # Categorical monotonicity can be partial order.
      monotonicities=[(0, 1), (0, 2)],
      # Categorical calibration layer supports standard Keras regularizers.
      kernel_regularizer=keras.regularizers.l1_l2(l1=0.001),
      kernel_initializer='constant')
  combined_calibrators.append(calibrator)

  # Create Lattice layer to nonlineary fuse output of calibrators. Don't forget
  # to specify monotonicity 'increasing' for any dimension which calibrator is
  # monotonic regardless of monotonicity direction of calibrator. This includes
  # partial monotonicity of CategoricalCalibration layer.
  lattice = tfl.layers.Lattice(
      lattice_sizes=lattice_sizes,
      monotonicities=['increasing', 'none', 'increasing', 'increasing',
                      'increasing', 'increasing', 'increasing'],
      output_min=0.0,
      output_max=1.0)

  model = keras.models.Sequential()
  # We have just 2 layer as far as Sequential model is concerned.
  # PWLConcatenate layer takes care of grouping calibrators.
  model.add(combined_calibrators)
  model.add(lattice)
  model.compile(loss=keras.losses.mean_squared_error,
                optimizer=keras.optimizers.Adagrad(learning_rate=1.0))

  features = training_data_df[
      ['age', 'sex', 'cp',
       'trestbps', 'chol', 'fbs', 'restecg']].values.astype(np.float32)
  target = training_data_df[['target']].values.astype(np.float32)

  model.fit(features,
            target,
            batch_size=32,
            epochs=FLAGS.num_epochs,
            validation_split=0.2,
            shuffle=False)


if __name__ == '__main__':
  app.run(main)
