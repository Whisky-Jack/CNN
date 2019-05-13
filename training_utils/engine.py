'''
Author: Wojciech Fedorko
Collaborators: Julian Ding, Abhishek Kajal
'''

# ======================== UNUSED IMPORTS (currently) =======================
import copy
import re

import numpy as np
from statistics import mean

import sklearn
from sklearn.metrics import roc_curve

import shutil

from torch.autograd import Variable
# ===========================================================================

# ======================== TEST IMPORTS =====================================
import collections
import sys
# ===========================================================================

import torch
from torch import optim
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.utils.data.sampler import SubsetRandomSampler

import os
import time

from iotools.data_handling import WCH5Dataset
from utils.notebook_utils import CSVData


class Engine:
    """The training engine 
    
    Performs training and evaluation
    """

    def __init__(self, model, config):
        self.model = model
        print(config.gpu)
        print(config.gpu_list)
        if config.gpu and config.gpu_list:
            print("requesting gpu ")
            print("gpu list: ")
            print(config.gpu_list)
            self.devids = ["cuda:{0}".format(x) for x in config.gpu_list]

            print("main gpu: "+self.devids[0])
            if torch.cuda.is_available():
                self.device = torch.device(self.devids[0])
                if len(self.devids) > 1:
                    print("using DataParallel on these devices: {}".format(self.devids))
                    self.model = nn.DataParallel(self.model, device_ids=config.gpu_list, dim=0)

                print("cuda is available")
            else:
                self.device=torch.device("cpu")
                print("cuda is not available")
        else:
            print("will not use gpu")
            self.device=torch.device("cpu")

        print(self.device)

        self.model.to(self.device)

        self.optimizer = optim.Adam(self.model.parameters(),eps=1e-3)
        self.criterion = nn.CrossEntropyLoss()
        self.softmax = nn.Softmax(dim=1)

        #placeholders for data and labels
        self.data=None
        self.labels=None
        self.iteration=None

        # NOTE: The functionality of this block is coupled to the implementation of WCH5Dataset in the iotools module
        self.dset=WCH5Dataset(config.path,
                              config.val_split,
                              config.test_split)

        self.train_iter=DataLoader(self.dset,
                                   batch_size=config.batch_size_train,
                                   shuffle=False,
                                   sampler=SubsetRandomSampler(self.dset.train_indices))
        
        self.val_iter=DataLoader(self.dset,
                                 batch_size=config.batch_size_val,
                                 shuffle=False,
                                 sampler=SubsetRandomSampler(self.dset.val_indices))
        
        self.test_iter=DataLoader(self.dset,
                                  batch_size=config.batch_size_test,
                                  shuffle=False,
                                  sampler=SubsetRandomSampler(self.dset.test_indices))

        

        self.dirpath=config.save_path
        
        self.data_description=config.data_description


        
        try:
            os.stat(self.dirpath)
        except:
            print("making a directory for model data: {}".format(self.dirpath))
            os.mkdir(self.dirpath)

        #add the path for the data type to the dirpath
        self.start_time_str = time.strftime("%Y%m%d_%H%M%S")
        self.dirpath=self.dirpath+'/'+self.data_description + "/" + self.start_time_str

        try:
            os.stat(self.dirpath)
        except:
            print("making a directory for model data for data prepared as: {}".format(self.data_description))
            os.makedirs(self.dirpath,exist_ok=True)

        self.config=config


    def forward(self,train=True):
        """
        Args: self should have attributes, model, criterion, softmax, data, label
        Returns: a dictionary of predicted labels, softmax, loss, and accuracy
        """
        with torch.set_grad_enabled(train):
            # Move the data and the labels to the GPU
            self.data = self.data.to(self.device)
            self.label = self.label.to(self.device)
                        
            # Prediction
            #print("this is the data size before permuting: {}".format(data.size()))
            self.data = self.data.permute(0,3,1,2)
            #print("this is the data size after permuting: {}".format(data.size()))
            prediction = self.model(self.data)
            # Training
            loss = -1
            loss = self.criterion(prediction,self.label)
            self.loss = loss
            
            softmax    = self.softmax(prediction).cpu().detach().numpy()
            prediction = torch.argmax(prediction,dim=-1)
            accuracy   = (prediction == self.label).sum().item() / float(prediction.nelement())        
            prediction = prediction.cpu().detach().numpy()
        
        return {'prediction' : prediction,
                'softmax'    : softmax,
                'loss'       : loss.cpu().detach().item(),
                'accuracy'   : accuracy}

    def backward(self):
        self.optimizer.zero_grad()  # Reset gradients accumulation
        self.loss.backward()
        self.optimizer.step()
        
    # ========================================================================
    def train(self, epochs=3.0, report_interval=10, valid_interval=100, save_interval=1000):
        # CODE BELOW COPY-PASTED FROM [HKML CNN Image Classification.ipynb]
        # (variable names changed to match new Engine architecture. Added comments and minor debugging)
        
        # Prepare attributes for data logging
        self.train_log, self.val_log = CSVData(self.dirpath+'/log_train.csv'), CSVData(self.dirpath+'/val_test.csv')
        # Set neural net to training mode
        self.model.train()
        # Initialize epoch counter
        epoch = 0.
        # Initialize iteration counter
        iteration = 0
        # Training loop
        while (int(epoch+0.5) < epochs):
            print('Epoch',int(epoch+0.5),'Starting @',time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            # Loop over data samples and into the network forward function
            for i, data in enumerate(self.train_iter):
                
                # Data and label
                self.data = data[0]
                self.label = data[1].long()
                
                # Move the data and labels on the GPU
                self.data = self.data.to(self.device)
                self.label = self.label.to(self.device)
                
                # Call forward: make a prediction & measure the average error
                res = self.forward(True)
                # Call backward: backpropagate error and update weights
                self.backward()
                # Epoch update
                epoch += 1./len(self.train_iter)
                iteration += 1
                
                # Log/Report
                #
                # Record the current performance on train set
                self.train_log.record(['iteration','epoch','accuracy','loss'],[iteration,epoch,res['accuracy'],res['loss']])
                self.train_log.write()
                # once in a while, report
                if i==0 or (i+1)%report_interval == 0:
                    print('... Iteration %d ... Epoch %1.2f ... Loss %1.3f ... Accuracy %1.3f' % (iteration,epoch,res['loss'],res['accuracy']))
                    
                # more rarely, run validation
                if (i+1)%valid_interval == 0:
                    with torch.no_grad():
                        self.model.eval()
                        val_data = next(iter(self.val_iter))
                        
                        # Data and label
                        self.data = val_data[0]
                        self.label = val_data[1].long()
                        
                        res = self.forward(False)
                        self.val_log.record(['iteration','epoch','accuracy','loss'],[iteration,epoch,res['accuracy'],res['loss']])
                        self.val_log.write()
                    self.model.train()
                if epoch >= epochs:
                    break
                    
                # Save on the given intervals
                if(i+1)%save_interval == 0:
                    self.save_state(curr_iter=iteration)
                    
            print('... Iteration %d ... Epoch %1.2f ... Loss %1.3f ... Accuracy %1.3f' % (iteration,epoch,res['loss'],res['accuracy']))
            
        self.val_log.close()
        self.train_log.close()
    
    # ========================================================================

    def validate(self):
        r"""Test the trained model on the validation set.
        
        Parameters: None
        
        Outputs : 
            total_val_loss = accumulated validation loss
            avg_val_loss = average validation loss
            total_val_acc = accumulated validation accuracy
            avg_val_acc = accumulated validation accuracy
            
        Returns : None
        """
        # Variables to output at the end
        val_loss = 0.0
        val_acc = 0.0
        val_iterations = 0
        
        # Iterate over the validation set to calculate val_loss and val_acc
        with torch.no_grad():
            
            # Set the model to evaluation mode
            self.model.eval()
            
            # Extract the event data and label from the DataLoader iterator
            for val_data in iter(self.val_iter):
                
                sys.stdout.write("\r\r\r" + "val_iterations : " + str(val_iterations))
                
                self.data, self.label = val_data[0:2]
                self.label = self.label.long()
                
                counter = collections.Counter(self.label.tolist())
                sys.stdout.write("\ncounter : " + str(counter))

                # Run the forward procedure and output the result
                result = self.forward(False)
                val_loss += result['loss']
                val_acc += result['accuracy']
                
                val_iterations += 1
         
        print("\nTotal val loss : ", val_loss,
              "\nTotal val acc : ", val_acc,
              "\nAvg val loss : ", val_loss/val_iterations,
              "\nAvg val acc : ", val_acc/val_iterations)
        
    def test(self):
        r"""Test the trained model on the test dataset.
        
        Parameters: None
        
        Outputs : 
            total_test_loss = accumulated validation loss
            avg_test_loss = average validation loss
            total_test_acc = accumulated validation accuracy
            avg_test_acc = accumulated validation accuracy
            
        Returns : None
        """
        # Variables to output at the end
        test_loss = 0.0
        test_acc = 0.0
        test_iterations = 0
        
        # Iterate over the validation set to calculate val_loss and val_acc
        with torch.no_grad():
            
            # Set the model to evaluation mode
            self.model.eval()
            
            # Extract the event data and label from the DataLoader iterator
            for test_data in iter(self.test_iter):
                
                sys.stdout.write("\r\r\r" + "test_iterations : " + str(test_iterations))
                
                self.data, self.label = test_data[0:2]
                self.label = self.label.long()
                
                counter = collections.Counter(self.label.tolist())
                sys.stdout.write("\ncounter : " + str(counter))

                # Run the forward procedure and output the result
                result = self.forward(False)
                test_loss += result['loss']
                test_acc += result['accuracy']
                
                test_iterations += 1
         
        print("\nTotal test loss : ", val_loss,
              "\nTotal test acc : ", val_acc,
              "\nAvg test loss : ", val_loss/val_iterations,
              "\nAvg test acc : ", val_acc/val_iterations)
        
    # ========================================================================
    
            
    def save_state(self, curr_iter=0):
        filename='state'+str(curr_iter)
        # Save parameters
        # 0+1) iteration counter + optimizer state => in case we want to "continue training" later
        # 2) network weight
        torch.save({
            'global_step': self.iteration,
            'optimizer': self.optimizer.state_dict(),
            'state_dict': self.model.state_dict()
        }, filename)
        return filename

    def restore_state(self,weight_file):
        # Open a file in read-binary mode
        with open(weight_file, 'rb') as f:
            # torch interprets the file, then we can access using string keys
            checkpoint = torch.load(f)
            # load network weights
            self.model.load_state_dict(checkpoint['state_dict'], strict=False)
            # if optim is provided, load the state of the optim
            if self.optimizer is not None:
                self.optimizer.load_state_dict(checkpoint['optimizer'])
            # load iteration count
            self.iteration = checkpoint['global_step']
            