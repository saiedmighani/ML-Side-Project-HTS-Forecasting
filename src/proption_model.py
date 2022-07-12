from ast import For
from asyncio import FastChildWatcher
from random import paretovariate
from tkinter import N
from grpc import Future
from matplotlib.pyplot import axis
from torch.nn import (
    LSTM, 
    MultiheadAttention, 
    Linear
)
from torch import tensor, rand, zeros, matmul, permute, log, lgamma, cat
from torch import nn
from torch import optim 
from tqdm import trange
import torch.nn.functional as F
from dirichlet import * 
import numpy as np 
import math 

def get_loss(output, target): 

    drichilet = Dirichlet(output) 

    # print(f"the batch shape is {output.shape[:-1]}")
    # print(f"the event shape is {output.shape[-1:]}")
    # print(drichilet)

    loss = drichilet.log_prob(target)
    loss_sum = loss.sum(-1)

    return -loss_sum

class hts_embedding(nn.Module): 

    """
    The embedding class for the time series 
    input: 
    - num_embeeddings and the dimension of embeddings 
    - x: the input that represents the index of the time series, starting from zero 

    """

    def __init__(self, num_embedd, embedd_dim, ) -> None:
        super(hts_embedding,self).__init__()

        self.num_embedd = num_embedd 
        self.embedd_dim = embedd_dim 
        self.embedd_layer = nn.Embedding(self.num_embedd, self.embedd_dim, max_norm=True)

    def forward(self, x): 

        output = self.embedd_layer(x)

        return output    

# unidirectional implementation of the LSTM Seq-2-Seq Network 
class encoder_lstm(nn.Module): 

    ''' Encodes time-series sequence '''

    def __init__(self, lstm_input_dim, lstm_hidden_dim, lstm_num_layer, batch_first=False) -> None:

        super(encoder_lstm, self).__init__()

        '''
        : param lstm_input_dim:     the number of features in the input X
        : param lstm_hidden_dim:    the number of features in the hidden state h
        : param lstm_num_layers:     number of recurrent layers (i.e., 2 means there are
        :                       2 stacked LSTMs)
        : batch_first:          True --> first input would be the number of sequences/observations
        '''

        self.lstm_input_dim = lstm_input_dim
        self.lstm_hidden_dim = lstm_hidden_dim
        self.lstm_num_layer = lstm_num_layer 
        self.batch_first = batch_first 

        self.lstm = LSTM(self.lstm_input_dim, self.lstm_hidden_dim, self.lstm_num_layer, batch_first = self.batch_first ,)

    def init_hidden(self, batch_size):
        
        '''
        initialize hidden state
        : param batch_size:    x_input.shape[1]
        : return:              zeroed hidden state and cell state 
        '''
        
        return (torch.randn(1*self.lstm_num_layer, batch_size, self.lstm_hidden_dim),
                torch.randn(1*self.lstm_num_layer, batch_size, self.lstm_hidden_dim))

    def forward(self, x, h0, c0): 

        '''
        : param x :                    input of shape (# in batch, seq_len, lstm_input_dim) 

        : return lstm_out,:            lstm_out gives all the hidden states in the sequence;
        
        : return self.cache            gives the hidden state and cell state for the last
        :                              element in the sequence 
        '''

        encoder_ouptut, (self.hn, self.cn) = self.lstm(x, (h0, c0))
        self.cache = (self.hn, self.cn)

        return encoder_ouptut, self.cache 

    
class decoder_lstm(nn.Module): 

    " Decodes time-series sequence"

    def __init__(self, lstm_input_dim, lstm_hidden_dim, lstm_num_layer, lstm_ouptut_dim, batch_first = False) -> None:
        super(decoder_lstm, self).__init__()

        '''
        : param self.lstm_input_dim:     the number of features in the input X
        : param lstm_hidden_dim:    the number of features in the hidden state h
        : param lstm_num_layers:     number of recurrent layers (i.e., 2 means there are
        :                       2 stacked LSTMs)
        : batch_first:          True --> first input would be the number of sequences/observations
        '''

        self.lstm_input_dim = lstm_input_dim
        self.lstm_hidden_dim = lstm_hidden_dim 
        self.lstm_num_layer = lstm_num_layer 
        self.lstm_output_dim = lstm_ouptut_dim
        self.batch_first = batch_first 
        self.lstm = LSTM(self.lstm_input_dim, self.lstm_hidden_dim, self.lstm_num_layer, batch_first= self.batch_first,)
        self.linear = Linear(self.lstm_hidden_dim, self.lstm_input_dim) 

    def forward(self, x, encoder_cache): 

        """

        x: input at the last timestamp  input of shape should 2D (batch_size, input_size)
    
        encoder_output: cache of the encoder output 
            hn: (D time lstm_num_layers, # nathces, lstm_hidden_dim))
            cn: (D time lstm_num_layers, # nathces, lstm_hidden_dim))
            D = 2 if bi-directional, 1 if unidirectional 
        """
        (hn, cn) = encoder_cache

        if self.batch_first: 
            x = x.unsqueeze(1) 

        else: 
            x = x.unsqueeze(0) 

        decoder_lstm_output, (self.hn , self.cn) = self.lstm(x, (hn, cn))
        self.cache = (self.hn , self.cn)

        if self.batch_first: 
            decoder_lstm_output = decoder_lstm_output.squeeze(1) 
        else: 
            decoder_lstm_output = decoder_lstm_output.squeeze(0) 

        decoder_ouput = self.linear(decoder_lstm_output) 

        return decoder_ouput, self.cache


class mha(nn.Module): 

    def __init__(self, mha_embedd_dim, num_head, batch_first = False) -> None:
        super().__init__()

        assert mha_embedd_dim%num_head == 0 #Embedding dimension must be 0 modulo number of heads

        self.mha_embedd_dim = mha_embedd_dim
        self.num_head = num_head 
        self.batch_first = batch_first 
        self.mha = MultiheadAttention(self.mha_embedd_dim, self.num_head, batch_first=self.batch_first)

    def forward(self, x): 

        values, atten_wieghts = self.mha(x, x, x)

        return values, atten_wieghts

class fc_skip(nn.Module): 

    def __init__(self, mha_output_dim, residual_output_dim, activation=nn.ReLU()) -> None:
        super().__init__()

        self.mha_output_dim = mha_output_dim
        self.residual_output_dim = residual_output_dim
        self.activation = activation
        self.fc = Linear(mha_output_dim, residual_output_dim)

    def forward(self, x):  

        output = self.fc(x)
        output += x 
        output = self.activation(output) 

        return output

class linear_final(nn.Module): 

    def __init__(self, residual_output_dim, model_output_dim) -> None:
        super().__init__()

        self.residual_output_dim = residual_output_dim
        self.model_output_dim = model_output_dim
        self.linear = nn.Linear(self.residual_output_dim, self.model_output_dim)

    def forward(self, x): 

        output = self.linear(x)

        ouput  = F.softmax(ouput)

        return output 


class proportion_model(nn.Module): 

    def __init__(
        self, 
        num_hts_embedd, hts_embedd_dim, # ts embedding hyper pars
        lstm_input_dim, lstm_hidden_dim, lstm_num_layer, lstm_output_dim, # lstm hyper pars 
        mha_embedd_dim, num_head, num_attention_layer, # mha hyper pars 
        mha_output_dim, residual_output_dim, # skip connection hyper pars 
        model_ouput_dim # output later hyper pars  

    ) -> None:
        super(proportion_model, self).__init__() 

        """
        lstm_input_dim: input dimensiotn of the LSTM encoder 
        lstm_hidden_dim: fixed hidden dimension of the LSTM encoder and decoder 
        lstm_num_layer: number of the layers in the 
        
        
        """

        assert model_ouput_dim == 1 
        self.num_hts_embedd = num_hts_embedd
        self.hts_embedd_dim = hts_embedd_dim

        self.lstm_input_dim = lstm_input_dim 
        self.lstm_hidden_dim = lstm_hidden_dim 
        self.lstm_num_layer = lstm_num_layer 
        self.lstm_output_dim = lstm_output_dim

        self.mha_embedd_dim =  mha_embedd_dim 
        self.num_head = num_head
        self.num_attention_layer = num_attention_layer 
        self.mha_output_dim = mha_output_dim 
        self.residual_output_dim = residual_output_dim
       
        self.embedd_layer = hts_embedding(self.num_hts_embedd, self.hts_embedd_dim)

        self.encoder_lstm = encoder_lstm(self.lstm_input_dim, self.lstm_hidden_dim, self.lstm_num_layer, batch_first=False)
        self.decoder_lstm = decoder_lstm(self.lstm_input_dim, self.lstm_hidden_dim, self.lstm_num_layer, self.lstm_output_dim , batch_first=False)

        self.mha = mha(self.mha_embedd_dim, self.num_head, batch_first=False)
        self.fc_skip = fc_skip(self.mha_output_dim, self.residual_output_dim)

        self.linear = linear_final(self.residual_output_dim, self.mha_output_dim)

    def train(
        self, 
        input_tensor, target_tensor, 
        n_epochs, 
        target_len, batch_size, 
        learning_rate = 0.01,
        ): 

        """
        All implementation is based on Batch = False 
        input_tensor: 4D torch tensor of shape b, H, C, input_dim 
        target_tensor: 4D torch tensor of shape b, F, C, 1 
            b: time-batched input 
            C: number of the children 
            H: history data points 
            F: future data points 
        """

        losses = np.full(n_epochs, np.nan)

        optimizer = optim.Adam(self.parameters(), lr = learning_rate)

        n_batches = int(input_tensor.shape[0] / batch_size)

        with trange(n_epochs) as tr:

            for it in tr:

                batch_loss = 0.
                batch_loss_tf = 0.
                batch_loss_no_tf = 0.

                num_tf = 0
                num_no_tf = 0

                for b in range(n_batches):

                    print(f"Training Batch number: {b}")

                    ### select the dataset according to the batch size 
                    input_batch = input_tensor[b:b+batch_size, :, :, :]  
                    target_batch = target_tensor[b: b+batch_size, :, :, :]
                    #print(input_batch.shape)

                    ## initialise the ENCODER network hidden state and cell state 
                    decoder_ouputs = zeros(target_len, input_batch.shape[-2], self.lstm_output_dim)
                    h0, c0 = self.encoder_lstm.init_hidden(input_batch.shape[-2]) 

                    #### ------------------------ RESET the gradient ------------------- ##############
                    optimizer.zero_grad()

                    for batch_index in range(input_batch.shape[0]): 

                        print(f"Training Batch number: {b} | Example {batch_index}")
                        
                        # L, C, input_dim 
                        input = input_batch[batch_index,:,:,:-1]
                        target = target_batch[batch_index,:,:,:]
                        print(f"The RAW input batch diemsion is {input.shape}")
                        print(f"The input target diemsion is {target.shape}")

                        #print(input.shape)

                        ### get the time series embedding encoding 
                        hts_embedding_input = (input[0,:,-1]).long()
                        # print(hts_embedding_input.shape)
                        # print(hts_embedding_input)
                        #print(hts_embedding_input[0,0,:])

                        ## ----------- hts embeddings encoding --------------
                        embedd_vector = self.embedd_layer(hts_embedding_input)  
                        print(embedd_vector.shape)
                        embedd_vector = embedd_vector.expand(
                            input.shape[0], 
                            embedd_vector.shape[0],
                            embedd_vector.shape[1]
                        )
                        #print(embedd_vector.shape)
                        input = cat((input, embedd_vector) , -1) 
                        print(f"with ENCODED hierachy, input batch diemsion is {input.shape}")

                        # ------------ lstm encoder -------------------------
                        encoder_ouput, (encoder_hn, encoder_cn) = self.encoder_lstm.forward(input, h0, c0)
                        encoder_cache = (encoder_hn, encoder_cn)
                        print(f"The encoder final hidden state shape is {encoder_hn.shape}")
                        print(f"The encoder final cell state shape is {encoder_hn.shape}")

                        ## Decoder
                        decoder_input = input_batch[:,-1,:]
                
                        print(f"The decoder input shape is {decoder_input.shape}")
                        # forwrad prop through the time 
                        for t in range(target_len): 

                            decoder_output, (decoder_hn, decoder_cn) = self.decoder_lstm.forward(decoder_input, encoder_cache)
                            decoder_ouputs[:,t,:] = decoder_output 

                            encoder_cache = (decoder_hn, decoder_cn)  
                            decoder_input = decoder_output 

                        print(f'The decoder output dimension is {decoder_ouputs.shape}')
                        decoder_ouputs = decoder_ouputs.view(
                            decoder_ouputs.shape[1],
                            decoder_ouputs.shape[0],
                            decoder_ouputs.shape[2]
                        ) # batch dimension is sequence length 
                        print(f'Putting sequence length as the batch numer, the decoder output dimension is {decoder_ouputs.shape}')

                        # forward pass through n layers of attention 
                        attention_inputs = decoder_ouputs
                        print(f'Attention input has shape : {attention_inputs.shape}')
                        for i in range(self.num_attention_layer):
                            
                            value, attention_weights  = self.mha.forward(attention_inputs)
                            value = self.fc_skip.forward(value)
                            attention_inputs = value 
                            #print(attention_inputs.shape)

                        print(f"The value-ouput from the attention layer has shape {value.shape} ")
            
                        batch_output = self.linear(value) # L, C, 1

                        print(f"The value-ouput from the linear layer has shape {batch_output.shape} ")

                        print(batch_output)
                    
                        print(f"The ouput from the model has shape {batch_output.shape}")
                    #print(f"The ouput from the model is {batch_output}")

                    #loss = get_loss(batch_output, target_batch)

                    #batch_loss += loss

                #batch_loss.backward()

                #optimizer.step()
    
        return 



if __name__ == "__main__": 

    """
    Data Processing Script 
    
    """

    # dimension about the dataset
    no_child = 100
    History = 24 
    Forward = 12 
    covariate_dim = 4 
    
    ### ------- pre-processing of randomly generated dataset for testing --------- ####

    time_dimension = (History+Forward) * 3
    # proportions matrix T * C 
    proportions = F.softmax(torch.rand(time_dimension, no_child),)
    #print(proportions.shape)

    # parent sales matrix T*1
    parent  = torch.randint(10,(time_dimension,1))
    #print(parent.shape)
    
    # covariates matrix T * covariate_dim 
    covariate = torch.rand(time_dimension,covariate_dim)
    #print(covariate.shape) 

    # hie_index
    hie_index = torch.arange(no_child)
    #print(hie_index)

    proportions_3d = proportions.reshape(time_dimension, no_child, 1)
    #print(proportions_3d.shape)
    #print(proportions_3d[0,:,:].sum())

    parent_3d = (parent.unsqueeze(1)).expand(parent.shape[0], no_child, parent.shape[-1])
    #print(parent_3d.shape)
    
    data_3d = torch.cat((proportions_3d, parent_3d), dim=-1)
    #print(data_3d.shape)
    #print(data_3d[0,:,:])

    covariate_3d = covariate.unsqueeze(1).expand(covariate.shape[0], no_child, covariate.shape[-1])
    #print(covariate_3d.shape)
    #print(covariate_3d[0,:,:])

    data_3d = torch.cat((data_3d,covariate_3d), dim=-1)
    #print(data_3d.shape)
    #print(data_3d[0,:,:])

    hie_index_2d = hie_index.expand(time_dimension, no_child)
    hie_index_3d = hie_index_2d.reshape(hie_index_2d.shape[0], hie_index_2d.shape[-1], 1)
    #print(hie_index_3d.shape)
    #print(hie_index_3d[0,:,:])

    data_3d = torch.cat((data_3d,hie_index_3d), dim=-1)
    #print(data_3d.shape)
    #print(data_3d[0,:,:])

    number_observations = data_3d.shape[0] - (History+Forward)+1 

    data_3d_time_batched = torch.empty(
        number_observations, 
        History+Forward,
        data_3d.shape[1],
        data_3d.shape[2]
    )

    for i in range(number_observations): 

        data_3d_time_batched[i,:,:,:] = data_3d[i:i+History+Forward, :, :] 


    #print(data_3d_time_batched.shape)
    #print(data_3d_time_batched[-1,:,:,:].shape)

    if torch.equal(data_3d_time_batched[-1,-1,:,:], data_3d[-1,:,:]): 


        print('data correctly processed to generate time-bacted tensor')

        input_tensor = torch.empty(
            number_observations, 
            History,
            data_3d_time_batched.shape[-2],
            data_3d_time_batched.shape[-1]
        )
        
        ## We first use the recursive predicitng mechanism in LSTM, in the future we release more blocks that adapt to teacher-forcing/mixed training 
        target_tensor = torch.empty(
            number_observations, 
            Forward,
            data_3d_time_batched.shape[-2],
            1
            # data_3d_time_batched.shape[-1]
        )

        print(input_tensor.shape)
        print(target_tensor.shape)

        print('Entering the training pipeline')

        for i in range(data_3d_time_batched.shape[0]): 

            input_tensor[i] = data_3d_time_batched[i,:History,:,:]
            target_2d = data_3d_time_batched[i,History:,:,0]
            target_tensor[i] =  target_2d.reshape(target_2d.shape[0], target_2d.shape[1],1)

            # print(input_tensor.shape)
            # print(target_tensor.shape)

        print(input_tensor.shape)
        print(target_tensor.shape)
        # print(target_tensor[-1,0,:,:].sum())
        
        ###---------- dimension on the model hypter-parameters from the paper ------------ ######
        num_hts_embedd = no_child
        hts_embedd_dim = 8

        lstm_input_dim = 2 + covariate_dim + hts_embedd_dim 
        lstm_hidden_dim = 48
        lstm_num_layer = 1 
        lstm_output_dim = 64

        mha_embedd_dim = lstm_output_dim
        num_head = 4
        num_attention_layer = 1
        mha_output_dim = mha_embedd_dim 
        residual_output_dim = mha_output_dim
        model_ouput_dim = 1 
        
        # define the model object 
        p_model = proportion_model(
            num_hts_embedd, hts_embedd_dim, # ts embedding hyper pars
            lstm_input_dim, lstm_hidden_dim, lstm_num_layer, lstm_output_dim, # lstm hyper pars 
            mha_embedd_dim, num_head, num_attention_layer, # mha hyper pars 
            mha_output_dim, residual_output_dim, # skip connection hyper pars 
            model_ouput_dim # output later hyper pars  
        )

        ###---------- trainign parameters from the paper ------------ ######

        n_epochs = 10
        target_len = Forward
        batch_size = 4
        l_r = 0.00079
        
        # start training 
        p_model.train(
            input_tensor, 
            target_tensor, 
            n_epochs, 
            target_len, 
            batch_size, 
            learning_rate = l_r,
        )

    else: 

        raise "Missed processed data point, not all time points are batched"


    


        

