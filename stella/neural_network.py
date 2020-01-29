import os, glob
import numpy as np
from tqdm import tqdm
import tensorflow as tf
from tensorflow import keras
from scipy.interpolate import interp1d
from astropy.table import Table, Column

__all__ = ['ConvNN']

class ConvNN(object):
    """
    Creates and trains the convolutional
    neural network.
    """

    def __init__(self, ts, 
                 layers=None, optimizer='adam',
                 loss='binary_crossentropy', 
                 metrics=None, output_dir=None):
        """
        Creates and trains a Tensorflow keras model
        with either layers that have been passed in
        by the user or with default layers used in
        Feinstein et al. (2020; in prep.).

        Parameters
        ----------
        ts : stella.TrainingSet object
        training : float, optional
             Assigns the percentage of training set data for training.
             Default is 80%.
        validation : float, optional
             Assigns the percentage of training set data for validation.
             Default is 10%.
        layers : np.array, optional
             An array of keras.layers for the ConvNN.
        optimizer : str, optional
             Optimizer used to compile keras model. Default is 'adam'.
        loss : str, optional
             Loss function used to compile keras model. Default is
             'binary_crossentropy'.
        metrics: np.array, optional
             Metrics used to train the keras model on. If None, metrics are
             [accuracy, precision, recall].
        epochs : int, optional
             Number of epochs to train the keras model on. Default is 15.
        seed : int, optional
             Sets random seed for reproducable results. Default is 2.
        output_dir : path, optional
             The path to save models/histories/predictions to. Default is
             to create a hidden ~/.stella directory.

        Attributes
        ----------
        layers : np.array
        optimizer : str
        loss : str
        metrics : np.array
        training_matrix : stella.TrainingSet.training_matrix
        labels : stella.TrainingSet.labels
        image_fmt : stella.TrainingSet.cadences
        """
        self.ds = ts
        self.layers = layers
        self.optimizer = optimizer
        self.loss = loss
        self.metrics = metrics
        self.training_matrix = np.copy(ts.training_matrix)
        self.labels = np.copy(ts.labels)
        self.cadences = np.copy(ts.cadences)

        self.frac_balance = ts.frac_balance + 0.0

        self.tpeaks = ts.training_peaks
        self.training_ids = ts.training_ids
        self.prec_recall_curve = None
        self.history = None
        self.history_table = None

        self.predval_fmt = 'predval_s{0:04d}_i{1:04d}_b{2}.txt'
        self.predtest_fmt = 'predtest_s{0:04d}_i{1:04d}_b{2}.txt'

        if output_dir is None:
            self.fetch_dir()
        else:
            self.output_dir = output_dir


    def create_model(self, seed):
        """
        Creates the Tensorflow keras model with appropriate layers.
        
        Attributes
        ----------
        model : tensorflow.python.keras.engine.sequential.Sequential
        """
        # SETS RANDOM SEED FOR REPRODUCABLE RESULTS
        np.random.seed(seed)
        tf.random.set_seed(seed)

        # INITIALIZE CLEAN MODEL
        keras.backend.clear_session()

        model = keras.models.Sequential()

        # DEFAULT NETWORK MODEL FROM FEINSTEIN ET AL. (in prep)
        if self.layers is None:
            filter1 = 16
            filter2 = 64
            dense   = 32
            dropout = 0.1

            # CONVOLUTIONAL LAYERS
            model.add(tf.keras.layers.Conv1D(filters=filter1, kernel_size=3, 
                                             activation='relu', padding='same', 
                                             input_shape=(self.cadences, 1)))
            model.add(tf.keras.layers.MaxPooling1D(pool_size=2))
            model.add(tf.keras.layers.Dropout(dropout))
            model.add(tf.keras.layers.Conv1D(filters=filter2, kernel_size=3, 
                                             activation='relu', padding='same'))
            model.add(tf.keras.layers.MaxPooling1D(pool_size=2))
            model.add(tf.keras.layers.Dropout(dropout))
            
            # DENSE LAYERS AND SOFTMAX OUTPUT
            model.add(tf.keras.layers.Flatten())
            model.add(tf.keras.layers.Dense(dense, activation='relu'))
            model.add(tf.keras.layers.Dropout(dropout))
            model.add(tf.keras.layers.Dense(1, activation='sigmoid'))
            
        else:
            for l in self.layers:
                model.add(l)
                
        # COMPILE MODEL AND SET OPTIMIZER, LOSS, METRICS
        if self.metrics is None:
            model.compile(optimizer=self.optimizer,
                          loss=self.loss,
                          metrics=['accuracy', tf.keras.metrics.Precision(), 
                                   tf.keras.metrics.Recall()])
        else:
            model.compile(optimizer=self.optimizer,
                          loss=self.loss,
                          metrics=self.metrics)

        self.model = model

        # PRINTS MODEL SUMMARY
        model.summary()


    def load_model(self, modelname, mode='validation'):
        """
        Loads an already created model. 

        Parameters
        ----------
        modelname : str
        mode : str, optional
        """
        model = keras.models.load_model(modelname)
        self.model = model
        
        if mode == 'test':
            pred = model.predict(self.ds.test_data)
        elif mode == 'validation':
            pred = model.predict(self.ds.val_data)


        ## Calculate metrics from here
        return 
        

    def train_models(self, seeds=[2], epochs=350, batch_size=64, shuffle=True,
                     pred_test=False, save=False):
        """
        Runs n number of models with given initial random seeds of
        length n. Also saves each model run to a hidden ~/.stella 
        directory. 

        Parameters
        ----------
        seeds : np.array
             Array of random seed starters of length n, where
             n is the number of models you want to run.
        epochs : int, optional
             Number of epochs to train for. Default is 350.
        batch_size : int, optional
             Setting the batch size for the training. Default
             is 64.
        shuffle : bool, optional
             Allows for shuffling of the training set when fitting
             the model. Default is True.
        pred_test : bool, optional
             Allows for predictions on the test set. DO NOT SET TO
             TRUE UNTIL YOU'VE DECIDED ON YOUR FINAL MODEL. Default
             is False.
        save : bool, optional
             Saves the predictions and histories of from each model
             in an ascii table to the specified output directory.
             Default is False.

        Attributes
        ----------
        history_table : Astropy.table.Table
             Saves the metric values for each model run.
        val_pred_table : Astropy.table.Table
             Predictions on the validation set from each run.
        test_pred_table : Astropy.table.Table
             Predictions on the test set from each run. Must set
             pred_test = True, or else it is an empty table.
        """

        if type(seeds) == int or type(seeds) == float or type(seeds) == np.int64: 
            seeds = np.array([seeds])

        self.epochs = epochs

        # CREATES TABLES FOR SAVING DATA
        table = Table()
        val_table  = Table([self.ds.val_ids, self.ds.val_labels, self.ds.val_tpeaks],
                           names=['tic', 'gt', 'tpeak'])
        test_table = Table([self.ds.test_ids, self.ds.test_labels, self.ds.test_tpeaks],
                           names=['tic', 'gt', 'tpeak'])
        
        
        for seed in seeds:
            
            fmt_tail = '_s{0:04d}_i{1:04d}_b{2}'.format(int(seed), int(epochs), self.frac_balance)
            model_fmt = 'model' + fmt_tail + '.h5'

            keras.backend.clear_session()
            
            # CREATES MODEL BASED ON GIVEN RANDOM SEED
            self.create_model(seed)
            self.history = self.model.fit(self.ds.train_data, self.ds.train_labels, 
                                          epochs=epochs,
                                          batch_size=batch_size, shuffle=shuffle,
                                          validation_data=(self.ds.val_data, self.ds.val_labels))

            col_names = list(self.history.history.keys())
            for cn in col_names:
                col = Column(self.history.history[cn], name=cn+'_s{0:04d}'.format(int(seed)))
                table.add_column(col)

            # SAVES THE MODEL TO OUTPUT DIRECTORY
            self.model.save(os.path.join(self.output_dir, model_fmt))

            # GETS PREDICTIONS FOR EACH VALIDATION SET LIGHT CURVE
            val_preds = self.model.predict(self.ds.val_data)
            val_table.add_column(Column(val_preds, name='pred_s{0:04d}'.format(int(seed))))
            

            # GETS PREDICTIONS FOR EACH TEST SET LIGHT CURVE IF PRED_TEST IS TRUE
            if pred_test is True:
                test_preds = self.model.predict(self.ds.test_data)
                test_table.add_column(Column(test_preds, name='test_s{0:04d}'.format(int(seed))))
                
        # SETS TABLE ATTRIBUTES
        self.history_table = table
        self.val_pred_table = val_table
        self.test_pred_table = test_table

        # SAVES TABLE IS SAVE IS TRUE
        if save is True:
            fmt_table = '_i{0:04d}_b{1}.txt'.format(int(epochs), self.frac_balance)
            hist_fmt = 'histories' + fmt_table
            pred_fmt = 'predval' + fmt_table

            table.write(os.path.join(self.output_dir, hist_fmt), format='ascii')
            val_table.write(os.path.join(self.output_dir, pred_fmt), format='ascii',
                            fast_writer=False)

            if len(test_table.colnames) > 3:
                test_fmt = 'predtest' + fmt_table
                test_table.write(os.path.join(self.output_dir, test_fmt), format='ascii',
                                 fast_writer=False)


    def create_df(self, threshold, mode='metrics', data_set='validation'):
        """
        Creates an astropy.Table.table for the ensemble metrics.

        Parameters
        ----------
        threshold : float
             Percentage cutoff for the ensemble metrics. Recommended 0.5.
        mode : str, optional
             Makes sure there are models to run metric calculations
             on. Default is 'metrics'.
        data_set : str, optional
             Allows the user to look at either the validation or test
             set metrics. Default is 'validation'. The other option is 'test'.

        Returns
        -------
        df : astropy.Table.table
             Table of predicted values from each model run.
        """
        
        if data_set.lower() == 'validation':
            files = np.sort(glob.glob(os.path.join(self.output_dir,
                                                   "predval*i{0:04d}*_b{1}.txt".format(self.epochs,
                                                                                       self.frac_balance))))
        elif data_set.lower() == 'test':
            files = np.sort(glob.glob(os.path.join(self.output_dir,
                                                   "predtest*i{0:04d}*_b{1}.txt".format(self.epochs,
                                                                                        self.frac_balance))))

        if mode is 'metrics' and len(files) < 2:
            raise ValueError("Can only calculate metrics for multiple models.")
        
        
        df= Table()
        mean_arr = []
        
        for i, val in enumerate(files):
            r = Table.read(val, format="csv")
            
            df.add_column(Column(np.round(r['pred'].data, 3),
                                 name='s'+(val.split('_s')[-1]).split('_')[0]))
            
            mean_arr.append(np.round(r['pred'].data, 3))
            
        df.add_column(Column(np.nanmean(mean_arr, axis=0), name='pred'))
        
        df.add_column(Column(r['gt'].data, name='gt'), index=0)
        df.add_column(Column(r['tpeak'].data, name='peak'), index=0)
        df.add_column(Column(r['# tic'].data, name='tic'), index=0)

        pred_round = np.zeros(len(df))
        pred_round[df['pred'] >= threshold] = 1
        pred_round[df['pred'] < threshold]  = 0
        df.add_column(Column(pred_round, name='pred_round'), index=0)

        return df

    def ensemble_metrics(self, df):
        """
        Calculates the metrics and average metrics when ensemble training.

        Parameters
        ----------
        df : astropy.Table.table
             Table of output predictions from the validation set.

        Attributes
        ----------
        average_precision : float
        accuracy : float
        recall_score : float
        precision_score : float
        prec_recall_curve : np.array
             2D array of precision and recall for plotting purposes.
        """
        from sklearn.metrics import precision_recall_curve
        from sklearn.metrics import average_precision_score
        from sklearn.metrics import precision_score
        from sklearn.metrics import recall_score

        ap, ac = [], []

        rs, ps = [], []
        for i, val in enumerate(df.columns[4:]):
    
            # CALCULATES AVERAGE PRECISION SCORE
            ap.append(np.round(average_precision_score(df['gt'].data, 
                                                       df[val].data, average=None), 4))
            # ROUNDED BASED ON THRESHOLD
            arr = np.copy(df[val].data)
            arr[arr >= threshold] = 1.0
            arr[arr < threshold] = 0.0
        
            # CALCULATES ACCURACY
            ac.append(np.round(np.sum(arr == df['gt'].data) / len(df), 4))

        # CALCULATES RECALL SCORE
        rs = np.round(recall_score(df['gt'], df['pred_round']), 4)
        
        # CALCULATES PRECISION SCORE
        ps = np.round(precision_score(df['gt'], df['pred_round']), 4)

        # PRECISION RECALL CURVE
        prec_curve, rec_curve, _ = precision_recall_curve(df['gt'], df['pred'])

        self.average_precision = ap[-1]
        self.accuracy = ac[-1]
        self.recall_score = rs
        self.precision_score = ps
        self.prec_recall_curve = np.array([rec_curve, prec_curve])
 

    def cross_validation(self, epochs=350, batch_size=64,
                         n_splits=5, shuffle=True):
        """
        Performs cross validation for a given number of K-folds.
        Reassigns the training and validation sets for each fold.

        Parameters
        ----------
        epochs : int, optional
             Number of epochs to run each folded model on. Default is 350.
        batch_size : int, optional
             The batch size for training. Default is 64.
        n_splits : int, optional
             Number of folds to perform. Default is 5.
        shuffle : bool, optional
             Allows for shuffling in scikitlearn.model_slection.KFold.
             Default is True.

        Attributes
        ----------
        kfolds_table : astropy.Table.table
             Table of history values from the model run on each fold.
        """
        from sklearn.model_selection import KFold
        from sklearn.metrics import precision_recall_curve
        from sklearn.metrics import average_precision_score

        num_flares = len(self.labels)
        trainval_cutoff = int(0.90 * num_flares)

        tab = Table()

        x_trainval = self.training_matrix[0:trainval_cutoff]
        y_trainval = self.labels[0:trainval_cutoff]
        p_trainval = self.tpeaks[0:trainval_cutoff]
        t_trainval = self.training_ids[0:trainval_cutoff]

        kfolds_histories = []
        kfolds_predictions = []

        kf = KFold(n_splits=n_splits, shuffle=shuffle)

        broken = self.predval_fmt.split('.')
        kfolds_fmt = broken[0].format(int(self.seed),
                                      int(epochs),
                                      self.frac_balance) + '_kfold{0:02d}.txt'

        i = 0
        for ti, vi in kf.split(y_trainval):
            # CREATES TRAINING AND VALIDATION SETS
            x_train   = x_trainval[ti]
            self.kfolds_train_labels = y_trainval[ti]
            x_val   = x_trainval[vi]
            self.kfolds_val_labels = y_trainval[vi]

            p_val = p_trainval[vi]
            t_val = t_trainval[vi]
            
            # REFORMAT TO ADD ADDITIONAL CHANNEL TO DATA
            self.kfolds_train_data = x_train.reshape(x_train.shape[0], x_train.shape[1], 1)
            self.kfolds_val_data = x_val.reshape(x_val.shape[0], x_val.shape[1], 1)
            
            # CREATES MODEL AND RUNS ON REFOLDED TRAINING AND VALIDATION SETS
            self.create_model()
            self.train_model(epochs=epochs, batch_size=batch_size,
                             shuffle=shuffle, kfolds=True)

            # CALCULATE METRICS FOR VALIDATION SET
            pred_val = self.model.predict(self.kfolds_val_data)
            precision, recall, _ = precision_recall_curve(self.kfolds_val_labels, pred_val)
            ap_final = average_precision_score(self.kfolds_val_labels, 
                                               pred_val, average=None)

            kfolds_histories.append(self.model.history.history)

            # SAVES KFOLDS PREDICTIONS
            np.savetxt(os.path.join(self.output_dir, kfolds_fmt.format(i)),
                       np.column_stack((t_val, pred_val, self.kfolds_val_labels, p_val)), 
                       fmt=['%.0f', '%.6f', '%.6f', '%.10f'], 
                       delimiter=',', header="tic,pred,gt,tpeak")

            col_names = list(self.model.history.history.keys())
            for cn in col_names:
                col = Column(self.model.history.history[cn], name=cn+'{0:03d}'.format(i))
                tab.add_column(col)

            i += 1

        self.kfolds_table = tab
        self.kfolds_histories = kfolds_histories


    def calibration(self, df, metric_threshold):
        """
        Transforming the rankings output by the CNN into actual probabilities.
        This can only be run for an ensemble of models.

        Parameters
        ----------
        df : astropy.Table.table
             Table of output predictions from the validation set.
        metric_threshold : float
             Defines ranking above which something is considered
             a flares.
        """
        # ADD COLUMN TO TABLE THAT CALCULATES THE FRACTION OF MODELS
        # THAT SAY SOMETHING IS A FLARE
        names= [i for i in df.colnames if 's' in i]
        flare_frac = np.zeros(len(df))

        for i, val in enumerate(len(df)):
            preds = np.array(list(df[names][i]))
            flare_frac[i] = len(preds[preds >= threshold]) / len(preds)

        df.add_column(Column(flare_frac, name='flare_frac'))
        
        # !! WORK IN PROGRESS !!

        return df
                       
        
    def predict(self, times, fluxes, errs, injected=False):
        """
        Takes in arrays of time and flux and predicts where the flares 
        are based on the keras model created and trained.

        Parameters
        ----------
        times : np.ndarray
             Array of times to predict flares in.
        fluxes : np.ndarray
             Array of fluxes to predict flares in.
        flux_errs : np.ndarray
             Array of flux errors for predicted flares.
        injected : bool, optional
             Returns predictions instead of setting attribute. Used
             for injection-recovery. Default is False.
             
        Attributes
        ----------
        predict_times : np.ndarray
             The input times array.
        predict_fluxes : np.ndarray
             The input fluxes array.
        predict_errs : np.ndarray
             The input flux errors array.
        predictions : np.ndarray
             An array of predictions from the model.
        """
        def fill_in_sample(t, f, e, sigma=2.5):
            # FILLS IN GAPS IN THE DATA FOR CHUNKING TO FIND FLARES
            t, f = np.array(t), np.array(f)
            
            diff = np.diff(t)
            
            diff_ind = np.where( diff >= (np.nanmedian(diff) + sigma*np.nanstd(diff)) )[0]
            avg_noise = np.nanstd(f) / 2.0
            
            if len(diff_ind) > 0:
                for i in diff_ind:
                    start = i
                    stop  = int(i + 2)
                    func = interp1d(t[start:stop], f[start:stop])
                    new_time = np.arange(t[start], 
                                         t[int(start+1)],
                                         np.nanmedian(diff))
                    new_flux = func(new_time) + np.random.normal(0, avg_noise,
                                                                 len(new_time))
                    t = np.insert(t, i, new_time)
                    f = np.insert(f, i, new_flux)
                    e = np.insert(e, i,
                                  np.full(len(new_time), avg_noise))
            t, f = zip(*sorted(zip(t, f)))
            return t, f, e


        predictions = []

        cadences = self.cadences + 0
        
        new_time = []
        new_flux = []
    
        for j in tqdm(range(len(times))):
            q = np.isnan(fluxes[j]) == False
            time = times[j]#[q]
            lc   = fluxes[j]#[q]
            err  = errs[j]#[q]
            
            time, lc, err = fill_in_sample(time, lc, err)
        
            # LIGHT CURVE MUST BE NORMALIZED
            lc = lc/np.nanmedian(lc)
    
            new_time.append(time)
            new_flux.append(lc)
            
            reshaped_data = np.zeros((len(lc), cadences))
            
            padding       = np.nanmedian(lc)
            std           = np.std(lc)/4.5
            cadence_pad   = int(cadences/2)
            
            for i in range(len(lc)):
                if i <= cadences/2:
                    fill_length   = int(cadence_pad-i)
                    padding_array = np.zeros( (fill_length,))
                    f = np.append(padding_array, lc[0:int(i+cadence_pad)])
                    
                    tsteps = np.std(np.diff(time)) * np.arange(0,fill_length,1)
                    tstep_padding = np.flip(time[i] - tsteps)
                    t = np.append(tstep_padding, time[0:int(i+cadence_pad)])
                    
                elif i >= (len(lc)-cadence_pad):
                    loc = [int(i-cadence_pad), int(len(lc))]
                    fill_length   = int(np.abs(cadences - len(lc[loc[0]:loc[1]])))
                    padding_array = np.zeros( (fill_length,))
                    f = np.append(lc[loc[0]:loc[1]], padding_array)
                    
                    tsteps = np.std(np.diff(time)) * np.arange(0,fill_length,1)
                    tstep_padding = time[i] - tsteps
                    t = np.append(time[loc[0]:loc[1]], tstep_padding)
                    
                else:
                    loc = [int(i-cadence_pad), int(i+cadence_pad)]
                    f = lc[loc[0]:loc[1]]
                    t = np.append(time[loc[0]:loc[1]], tstep_padding)
                    
                reshaped_data[i] = f
            
            reshaped_data = reshaped_data.reshape(reshaped_data.shape[0], 
                                                  reshaped_data.shape[1], 1)
            
            preds = self.model.predict(reshaped_data)
            preds = np.reshape(preds, (len(preds),))
            predictions.append(preds)
            
        return predictions


    def fetch_dir(self):
        """
        Returns the default path to the directory where files will be saved
        or loaded.
        By default, this method will return "~/.stella" and create
        this directory if it does not exist.  If the directory cannot be
        access or created, then it returns the local directory (".").
        
        Attributes
        -------
        output_dir : str
        Path to location of saved CNN models.
        """
        download_dir    = os.path.join(os.path.expanduser('~'), '.stella')
        if os.path.isdir(download_dir):
            self.output_dir = download_dir
        else:
            # if it doesn't exist, make a new cache directory
            try:
                os.mkdir(download_dir)
                # downloads locally if OS error occurs
            except OSError:
                download_dir = '.'
                warnings.warn('Warning: unable to create {}. '
                              'Saving models to the current '
                              'working directory instead.'.format(download_dir))
                    
            self.output_dir = download_dir
