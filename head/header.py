"""
contains original and test functions for NN

"""
import copy
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
from torch.jit import Final
from typing import List, Tuple, Any
from pem import PEM , PEM_mimo, mse


def R2(Y_sys, Yhat):
    s1 = np.sum((Y_sys - Yhat) ** 2)
    mean = np.mean(Y_sys)
    s2 = np.sum((Y_sys - mean) ** 2)
    return 1.0 - s1 / s2


def normalize(x, r=1):  # 1-dimension
    """
    normalize an array
    :param x: array
    :param r: new array of [-r, r]
    :return: new array
    """
    out = []
    mini = np.amin(x)
    maxi = np.amax(x)
    for j in range(len(x)):
        # norm = (x[i] - mini) / (maxi - mini)  # [0, 1]
        norm = 2 * r * (x[j] - mini) / (maxi - mini) - r
        out.append(norm)
    out = np.array(out, dtype=np.float32)
    return out


def get_batch(batch_num, batch_length, Y, U):  # , x_fit
    N = len(Y)
    batch_start = np.random.choice(np.arange(N - batch_length, dtype=np.int64), batch_num, replace=False)
    batch_index = batch_start[:, np.newaxis] + np.arange(batch_length)  # batch sample index
    batch_index = batch_index.T  # (batch_length, batch_num, n_x)
    batch_x0 = x_fit[batch_start, :]  # (batch_num, n_x), initials in each batch
    batch_x = x_fit[[batch_index]]
    batch_u = torch.tensor(U[batch_index, :])
    batch_y = torch.tensor(Y[batch_index])
    return batch_y#, batch_u #,batch_x0, batch_x,


#----- test----------
class MechanicalSystem_mimo(nn.Module):

    def __init__(self, dt, n_x=3,n_u=1, init_small=True):
        super(MechanicalSystem_mimo, self).__init__()
        self.dt = dt  # sampling time
        self.hidden = 64
        self.n_x = n_x
        self.n_u = n_u
        self.net = nn.Sequential(nn.Linear(n_x + n_u, self.hidden),  # 3*1
                                 # nn.LeakyReLU(negative_slope=0.4),
                                 nn.ReLU(),
                                 nn.Linear(self.hidden, n_u))

        if init_small:
            for i in self.net.modules():
                if isinstance(i, nn.Linear):
                    nn.init.normal_(i.weight, mean=0, std=1e-3)
                    nn.init.constant_(i.bias, val=0)

    def forward(self, x1, u1):
        list_dx: List[torch.Tensor]
        in_xu = torch.cat((x1, u1), -1)
        dv = self.net(in_xu) / self.dt  # v, dv = net(x, v)
        if self.n_x == 4:
            list_dx = [x1[..., [1]], x1[..., [2]],x1[..., [3]], dv]  # [dot x=v, dot v = a]
        if self.n_x == 3:
            list_dx = [x1[..., [1]], x1[..., [2]], dv]  # [dot x=v, dot v = a]
        dx = torch.cat(list_dx, -1)
        return dx


#  #------------- torch original >>>>>> -----------
class MechanicalSystem(nn.Module):  # original

    def __init__(self, dt, n_x=2, init_small=True):
        super(MechanicalSystem, self).__init__()
        self.dt = dt  # sampling time
        self.hidden = 64
        self.net = nn.Sequential(nn.Linear(n_x + 1, self.hidden),  # 3*1
                                 # nn.LeakyReLU(negative_slope=0.4),
                                 nn.ReLU(),
                                 nn.Linear(self.hidden, 1))

        if init_small:
            for i in self.net.modules():
                if isinstance(i, nn.Linear):
                    nn.init.normal_(i.weight, mean=0, std=1e-3)
                    nn.init.constant_(i.bias, val=0)

    def forward(self, x1, u1):
        list_dx: List[torch.Tensor]
        in_xu = torch.cat((x1, u1), -1)
        dv = self.net(in_xu) / self.dt  # v, dv = net(x, v)
        list_dx = [x1[..., [1]], dv]  # [dot x=v, dot v = a]
        dx = torch.cat(list_dx, -1)
        return dx


class ForwardEuler(nn.Module):  # original

    def __init__(self, model, dt):
        super(ForwardEuler, self).__init__()
        self.model = model
        self.dt = dt

    def forward(self, x0: torch.Tensor, u: torch.Tensor) -> torch.Tensor:
        xhat_list = list()
        x_step = x0
        for u_step in u.split(1):
            u_step = u_step.squeeze(0)  # size (1, batch_num, 1) -> (batch_num, 1)
            dx = self.model(x_step, u_step)
            x_step = x_step + dx * self.dt
            xhat_list += [x_step]

        xhat = torch.stack(xhat_list, 0)
        return xhat


class RK4(nn.Module):  # original, not often used.
    def __init__(self, model, dt):
        super(RK4, self).__init__()
        self.model = model
        self.dt = dt

    def forward(self, x0, u):
        xhat_list: List[torch.Tensor] = []
        # xhat_list = []
        x_step = x0
        for u_step in u.split(1):
            u_step = u_step.squeeze(0)
            xhat_list += [x_step]
            k1 = self.model(x_step, u_step)
            k2 = self.model(x_step + self.dt * k1 / 2.0, u_step)
            k3 = self.model(x_step + self.dt * k2 / 2.0, u_step)
            k4 = self.model(x_step + self.dt * k3, u_step)
            x_step = x_step + self.dt * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
            # xhat_list += [x_step]  #  give result, but far
        xhat = torch.stack(xhat_list, 0)
        return xhat


class NeuralStateSpaceModel(nn.Module):  # when not pos and vel, no derivative relation, not used yet
    n_x: Final[int]
    n_u: Final[int]
    n_feat: Final[int]

    def __init__(self, n_x=2, n_u=1, n_feat=64, scale_dx=1.0, init_small=True, activation='relu'):
        super(NeuralStateSpaceModel, self).__init__()
        self.n_x = n_x
        self.n_u = n_u
        self.n_feat = n_feat
        self.scale_dx = scale_dx

        if activation == 'relu':
            activation = nn.ReLU()
        elif activation == 'softplus':
            activation = nn.Softplus()
        elif activation == 'tanh':
            activation = nn.Tanh()

        self.net = nn.Sequential(
            nn.Linear(n_x + n_u, n_feat),  # 2 states, 1 input
            activation,
            nn.Linear(n_feat, n_x)
        )

        # Small initialization is better for multi-step methods
        if init_small:
            for m in self.net.modules():
                if isinstance(m, nn.Linear):
                    nn.init.normal_(m.weight, mean=0, std=1e-4)
                    nn.init.constant_(m.bias, val=0)

    def forward(self, in_x, in_u):
        in_xu = torch.cat((in_x, in_u), -1)  # concatenate x and u over the last dimension to create the [xu] input
        dx = self.net(in_xu)  # \dot x = f([xu])
        dx = dx * self.scale_dx
        return dx


class CascadedTanksOverflowNeuralStateSpaceModel(nn.Module):

    def __init__(self, n_feat=100, scale_dx=1.0, init_small=True):
        super(CascadedTanksOverflowNeuralStateSpaceModel, self).__init__()
        self.n_feat = n_feat
        self.scale_dx = scale_dx

        # Neural network for the first state equation = NN(x_1, u)
        self.net_dx1 = nn.Sequential(
            nn.Linear(2, n_feat),
            nn.ReLU(),
            # nn.Linear(n_feat, n_feat),
            # nn.ReLU(),
            nn.Linear(n_feat, 1),
        )

        # Neural network for the first state equation = NN(x_1, x2, u) # we assume that with overflow the input may influence the 2nd tank instantaneously
        self.net_dx2 = nn.Sequential(
            nn.Linear(3, n_feat),
            nn.ReLU(),
            # nn.Linear(n_feat, n_feat),
            # nn.ReLU(),
            nn.Linear(n_feat, 1),
        )

        # Small initialization is better for multi-step methods
        if init_small:
            for m in self.net_dx1.modules():
                if isinstance(m, nn.Linear):
                    nn.init.normal_(m.weight, mean=0, std=1e-4)
                    nn.init.constant_(m.bias, val=0)

        # Small initialization is better for multi-step methods
        if init_small:
            for m in self.net_dx2.modules():
                if isinstance(m, nn.Linear):
                    nn.init.normal_(m.weight, mean=0, std=1e-4)
                    nn.init.constant_(m.bias, val=0)

    def forward(self, in_x, in_u):

        # the first state derivative is NN_1(x1, u)
        in_1 = torch.cat((in_x[..., [0]], in_u), -1)  # concatenate 1st state component with input
        dx_1 = self.net_dx1(in_1)

        # the second state derivative is NN_2(x1, x2, u)
        in_2 = torch.cat((in_x, in_u), -1)  # concatenate states with input to define the
        dx_2 = self.net_dx2(in_2)

        # the state derivative is built by concatenation of dx_1 and dx_2, possibly scaled for numerical convenience
        dx = torch.cat((dx_1, dx_2), -1)
        dx = dx * self.scale_dx
        return dx

#
# ----------------------<<< original --------------------
#
# ---- <<<< torch original --test >>>>>----


# class ForwardEulerEvolution(nn.Module):  # only NN and retrain
#
#     def __init__(self, model,
#                  # factor,
#                  dt, N, optimizer, update=True, epoch=10000, threshold1=0, threshold2=0,
#                  sensitivity=500, slot=1500, lr=0.01):
#
#         super(ForwardEulerEvolution, self).__init__()
#         # self.factor = factor
#
#         self.model = model
#         self.dt = dt
#         self.N = N
#         self.optimizer = optimizer
#         self.update = update
#         self.epoch = epoch  # retraining epoches if evolute
#         self.threshold1 = threshold1  # start retrain
#         self.threshold2 = threshold2  # stop retrain
#         self.sensitivity = sensitivity  # monitor R2
#         self.slot = slot  # data size used for retraining
#         self.lr = lr
#         self.stop = []
#         self.evolute = []
#         self.xhat_data = torch.zeros((N, 2))
#
#     def forward(self, x0: torch.Tensor, u: torch.Tensor, y: torch.tensor):
#         x_step = x0
#
#         q = 0
#         while q < self.N:
#             u_step = u[q]
#
#             dx = self.model(x_step, u_step)
#             x_step = x_step + dx * self.dt
#             self.xhat_data[q, :] = x_step[0, :].clone().detach()
#             self.match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
#             # self.match = R2(y[0:q, 0, 0], self.xhat_data[0:q, 0])
#             print(f'R2 is {self.match} at data {q}')
#
#             # if self.update:
#             if self.update and self.match < self.threshold1:  # update
#                 for param in self.model.net[0].parameters():
#                     param.requires_grad = False
#
#                 self.evolute.append(q)
#                 print('evolute at ', q)
#
#                 # self.model.train()
#
#                 # model2.load_state_dict(self.model.state_dict())
#                 # model2.train()
#                 # model2.requires_grad = True
#                 # optimizer = torch.optim.Adam([{'params': params_net, 'lr': self.lr}], lr=self.lr * 10)
#
#                 # x_train = self.xhat_data[q - self.slot:q]
#                 # x_train = torch.tensor(x_train[:, None, :], requires_grad=True)
#
#                 retrain = ForwardEuler(model=self.model, dt=self.dt)
#                 # retrain = ForwardEulerFactor(model=self.model, dt=self.dt, factor=self.factor)
#
#                 batch_num = 64
#                 batch_length = 32
#
#                 for epoch in range(self.epoch):
#                     x_train = x_step.clone().detach()  #
#                     u_train = u[
#                               q - self.slot:q]  # copy.deepcopy()  # a small batch (slot, 1)????? or one element training???
#                     y_train = y[q - self.slot:q]  # copy.deepcopy()  #
#
#                     # batch_x0, batch_x, batch_u, batch_y = get_batch(batch_num=batch_num, batch_length=batch_length, u=u_train, y=y_train, total=self.slot, dt=self.dt)
#                     # batch_xhat = retrain(batch_x0, batch_u)
#                     # batch_yhat = batch_xhat[:, :, [0]]
#                     # error_out = batch_yhat - batch_y
#
#                     xhat = retrain(x_train, u_train)
#
#                     yhat = xhat[:, :, [0]]
#
#                     # with torch.no_grad():
#                     #     xhat = retrain(x_train, u_train)
#                     #     yhat = xhat[:, :, [0]]
#                     #     yhat = self.factor(yhat, u_train)
#
#                     error_out = yhat - y_train
#
#                     # ---- same as ForwardEuler-------
#                     # xhat_list = []
#                     # for u_train_step in u_train.split(1):
#                     #     u_train_step = u_train_step.squeeze(0)  # size (1, batch_num, 1) -> (batch_num, 1)
#                     #     # dx = self.model(x_train, u_train_step)
#                     #     dx = self.model(x_train, u_train_step)
#                     #     x_train = x_train + dx * self.dt
#                     #     xhat_list += [x_train]
#                     # xhat = torch.stack(xhat_list, 0)
#                     # ---------------------
#
#                     loss = torch.mean((error_out / 0.1) ** 2)  # error_scale = 0.1
#                     # if (epoch + 1) % 100 == 0:
#                     #     print(f'epoch {epoch + 1}/{self.epoch}: loss= {loss.item():.4f}, yhat= {yhat[:,0, 0]:.4f}')
#
#                     loss.backward(retain_graph=True)  #
#                     self.optimizer.step()
#                     # print('retrain epoch is', epoch)
#                     # print(R2(y[q - self.slot:q, 0, 0], self.xhat_data[q - self.slot:q, 0]))
#                     self.optimizer.zero_grad()
#
#                     if (epoch + 1) % 50 == 0:  # check for break
#                         print('loss', loss.item())
#                         # ??????????????????????
#                         with torch.no_grad():
#                             x_vali = retrain(x_train, u_train)
#                             y_vali = x_vali[:, :, 0]
#
#                             self.match = R2(y_train[:, 0, 0], y_vali[:, 0])  # [q - self.slot:q, 0, 0]??
#
#                             # u_vali = u[q - self.sensitivity:q]
#                             # x_vali = retrain(x_train, u_vali)
#                             # y_vali = x_vali[:, :, 0]
#                             # self.match = R2(y[q - self.sensitivity:q, 0, 0], y_vali[:, 0])  # [q - self.slot:q, 0, 0]
#
#                             print(f'epoch {epoch + 1}/{self.epoch}: R2= {self.match}')
#                             if self.match > self.threshold2:
#                                 self.stop.append(q)
#                                 print('evolute finish at ', q)
#                                 # self.model.eval()
#                                 break
#
#                 # with torch.no_grad():
#                 #     dx = self.model(x_step, u_step)
#                 #     x_step = x_step + dx * self.dt
#                 #     self.xhat_data[q, :] = x_step[0, :].clone().detach()  #
#                 #     self.match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                 #     # self.match = R2(y[0:q, 0, 0], self.xhat_data[0:q, 0])
#                 #
#                 #     if self.match > self.threshold2:
#                 #
#                 #         self.stop.append(q)
#                 #         print('stop evolute at ', q)
#                 #         # self.model.eval()
#                 #         break
#
#                 # self.model.load_state_dict(copy.deepcopy(model2.state_dict()))
#                 #
#             q = q + 1
#
#             # u_step = u[q]
#             #     # y_step = y[q]
#             # dx = self.model(x_step, u_step)
#             # x_step = x_step + dx * self.dt
#             # self.xhat_data[q, :] = x_step[0, :].clone().detach()  #
#             # self.match = R2(y[q - self.slot:q], self.xhat_data[q - self.slot:q])
#
#             # q = q + 1
#
#         return self.xhat_data


# ---------------->>>>> finished in project, name changed to forwardEulerPEM, check before use-------
# class ForwardEuler_PEM(nn.Module):  # test, original working is ForwardEulerPEM
#
#     def __init__(self, model,
#                  factor,
#                  dt, N, optimizer, update, threshold1=0, threshold2=0,
#                  sensitivity=100):
#
#         super(ForwardEuler_PEM, self).__init__()
#         self.factor = factor
#         self.model = model
#         self.dt = dt
#         self.N = N
#         self.optimizer = optimizer
#         self.update = update
#
#         self.threshold1 = threshold1  # start update
#         self.threshold2 = threshold2  # stop update
#         self.sensitivity = sensitivity  # monitor R2
#
#         self.stop = []
#         self.correction = []
#         self.xhat_data = np.zeros((N, 2))
#
#     def forward(self, x0: torch.Tensor, u: torch.Tensor, y):
#         x_step = x0
#         self.Thehat = np.zeros((self.N, 6))
#         # self.y_pem = np.zeros((self.N, 1))
#         # self.y_pem0 = np.zeros((self.N, 1))
#         # self.y_pem = np.empty(self.N)
#         # self.y_pem0 = np.empty(self.N)
#         self.y_pem = []
#         self.y_pem0 = []
#         self.r2 = np.zeros(self.N)
#         self.alter = torch.zeros(1, 2)
#         self.err = np.zeros(self.N)  # |y-yhat|
#         q = 0
#         while q < self.N:
#             # if not self.update:
#             if self.update == 0:  # not updating
#                 # simple forward Euler
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt
#                 self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()
#                 # self.match = R2(y[q - self.sensitivity:q], self.xhat_data[q - self.sensitivity:q, 0])
#                 # print(f'R2 is {self.match} at data {q}')
#                 q = q + 1
#
#             # if self.update:
#             if self.update == 1:  # PEM running all, consecutive structure # discard
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt
#                 # self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect output of NN
#                 # match0 = R2(y[q - self.sensitivity:q], self.xhat_data[q - self.sensitivity:q, 0])
#
#                 y_nn = x_step[:, 0].clone().detach().numpy()
#                 self.factor.forward(y[q], y_nn)
#                 self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                 self.xhat_data[q, :] = self.factor.Xhat_data[:, 0]
#                 self.match = R2(y[q - self.sensitivity:q], self.xhat_data[q - self.sensitivity:q, 0])
#                 # print(f'R2 is {self.match} at data {q}')
#                 # if self.match < self.threshold1:
#                 #     self.evolute.append(q)
#                 #     print('evolute at ', q)
#
#                 # u_step = u[q]
#                 # dx = self.model(x_step, u_step)
#                 # x_step = x_step + dx * self.dt
#                 # y_train = y[[q]]
#                 # u_train = u_step.clone().detach().numpy()
#                 # x_train = x_step.T.clone().detach().numpy()
#                 #
#                 # self.factor.forward(y_train, u_train, x_train)
#                 # print('Ahat[1, 0]', self.factor.Ahat[1, 0])
#                 # self.ahat.append(self.factor.Ahat[1, 0])
#                 # x_train = np.dot(self.factor.Ahat, x_train)  # ????
#                 # self.xhat_data[q, :] = x_train[:, 0]
#                 # self.match = R2(y[q - self.sensitivity:q], self.xhat_data[q - self.sensitivity:q, 0])
#                 # if self.match > self.threshold2:
#                 #     self.stop.append(q)
#                 #     print('evolute finish at ', q)
#                 #     # self.model.eval()
#                 #     break
#                 q = q + 1
#
#                 # with torch.no_grad():
#
#                 # x_vali = retrain(x_train, u_train)
#                 # y_vali = x_vali[:, :, 0]
#                 # self.match = R2(y_train[:, 0, 0], y_vali[:, 0])  #[q - self.slot:q, 0, 0]??
#                 # x_train = x_step.clone().detach()
#                 # u_vali = u[q - self.sensitivity:q]
#                 # x_vali = retrain(x_train, u_vali)
#                 # xhat_vali = np.dot(self.factor.Ahat, x_vali[:, 0, :].T.numpy())
#                 # self.match = R2(y[q - self.sensitivity:q, 0, 0], xhat_vali[0, :])  # [q - self.slot:q, 0, 0]
#
#                 # ----------------------------------------------
#
#                 # self.model.train()
#
#                 # model2.load_state_dict(self.model.state_dict())
#                 # model2.train()
#                 # model2.requires_grad = True
#                 # optimizer = torch.optim.Adam([{'params': params_net, 'lr': self.lr}], lr=self.lr * 10)
#
#                 # x_train = self.xhat_data[q - self.slot:q]
#                 # x_train = torch.tensor(x_train[:, None, :], requires_grad=True)
#                 #
#                 # retrain = ForwardEuler(model=self.model, dt=self.dt)
#                 # # retrain = ForwardEulerFactor(model=self.model, dt=self.dt, factor=self.factor)
#                 #
#                 # self.factor.Xhat_old = x_step.T.clone().detach().numpy()
#                 # u_train = self.xhat_data[q - self.slot:q, 0].clone().detach().numpy()
#                 # y_train = y[q - self.slot:q, 0, 0].clone().detach().numpy()
#                 # Xhat_data =self.factor.forward(y_train, u_train*0)  #
#                 # # yhat = Xhat_data[:, 0]
#                 # # print('after first trian', R2(y_train, yhat))
#
#                 # with torch.no_grad():
#                 #
#                 #     # x_vali = retrain(x_train, u_train)
#                 #     # y_vali = x_vali[:, :, 0]
#                 #     # self.match = R2(y_train[:, 0, 0], y_vali[:, 0])  #[q - self.slot:q, 0, 0]??
#                 #     # x_train = x_step.clone().detach()
#                 #     # u_vali = u[q - self.sensitivity:q]
#                 #     # x_vali = retrain(x_train, u_vali)
#                 #     # xhat_vali = np.dot(self.factor.Ahat, x_vali[:, 0, :].T.numpy())
#                 #     self.match = R2(y[q - self.sensitivity:q, 0, 0], xhat_vali[0, :])  # [q - self.slot:q, 0, 0]
#                 #
#                 #
#                 #     if self.match > self.threshold2:
#                 #         self.stop.append(q)
#                 #         print('evolute finish at ', q)
#                 #         # self.model.eval()
#                 #         break
#
#                 # for epoch in range(self.epoch):
#                 #       #
#                 #       # copy.deepcopy()  #
#                 #
#                 #     # batch_x0, batch_x, batch_u, batch_y = get_batch(batch_num=batch_num, batch_length=batch_length, u=u_train, y=y_train, total=self.slot, dt=self.dt)
#                 #     # batch_xhat = retrain(batch_x0, batch_u)
#                 #     # batch_yhat = batch_xhat[:, :, [0]]
#                 #     # error_out = batch_yhat - batch_y
#                 #
#                 #     xhat = retrain(x_train, u_train)
#                 #
#                 #     yhat = xhat[:, :, [0]]
#                 #
#                 #
#                 #     # with torch.no_grad():
#                 #     #     xhat = retrain(x_train, u_train)
#                 #     #     yhat = xhat[:, :, [0]]
#                 #     #     yhat = self.factor(yhat, u_train)
#                 #
#                 #
#                 #     error_out = yhat - y_train
#                 #
#                 #     # ---- same as ForwardEuler-------
#                 #     # xhat_list = []
#                 #     # for u_train_step in u_train.split(1):
#                 #     #     u_train_step = u_train_step.squeeze(0)  # size (1, batch_num, 1) -> (batch_num, 1)
#                 #     #     # dx = self.model(x_train, u_train_step)
#                 #     #     dx = self.model(x_train, u_train_step)
#                 #     #     x_train = x_train + dx * self.dt
#                 #     #     xhat_list += [x_train]
#                 #     # xhat = torch.stack(xhat_list, 0)
#                 #     # ---------------------
#                 #
#                 #     loss = torch.mean((error_out / 0.1) ** 2)  # error_scale = 0.1
#                 #     # if (epoch + 1) % 100 == 0:
#                 #     #     print(f'epoch {epoch + 1}/{self.epoch}: loss= {loss.item():.4f}, yhat= {yhat[:,0, 0]:.4f}')
#                 #
#                 #     loss.backward(retain_graph=True)  #
#                 #     self.optimizer.step()
#                 #     # print('retrain epoch is', epoch)
#                 #     # print(R2(y[q - self.slot:q, 0, 0], self.xhat_data[q - self.slot:q, 0]))
#                 #     self.optimizer.zero_grad()
#                 #
#                 #     if (epoch + 1) % 50 == 0:  # check for break
#                 #         print('loss', loss.item())
#                 #         # ??????????????????????
#                 #         with torch.no_grad():
#                 #
#                 #             # x_vali = retrain(x_train, u_train)
#                 #             # y_vali = x_vali[:, :, 0]
#                 #             # self.match = R2(y_train[:, 0, 0], y_vali[:, 0])  #[q - self.slot:q, 0, 0]??
#                 #
#                 #             u_vali = u[q - self.sensitivity:q]
#                 #             x_vali = retrain(x_train, u_vali)
#                 #             y_vali = x_vali[:, :, 0]
#                 #             self.match = R2(y[q - self.sensitivity:q, 0, 0], y_vali[:, 0])  # [q - self.slot:q, 0, 0]
#                 #
#                 #             print(f'epoch {epoch + 1}/{self.epoch}: R2= {self.match}')
#                 #             if self.match > self.threshold2:
#                 #                 self.stop.append(q)
#                 #                 print('evolute finish at ', q)
#                 #                 # self.model.eval()
#                 #                 break
#
#                 # with torch.no_grad():
#                 #     dx = self.model(x_step, u_step)
#                 #     x_step = x_step + dx * self.dt
#                 #     self.xhat_data[q, :] = x_step[0, :].clone().detach()  #
#                 #     self.match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                 #     # self.match = R2(y[0:q, 0, 0], self.xhat_data[0:q, 0])
#                 #
#                 #     if self.match > self.threshold2:
#                 #
#                 #         self.stop.append(q)
#                 #         print('stop evolute at ', q)
#                 #         # self.model.eval()
#                 #         break
#
#                 # self.model.load_state_dict(copy.deepcopy(model2.state_dict()))
#                 #
#             if self.update == 2:  # parallel, NN + PEM, # discard
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt
#                 y_nn = x_step[:, 0].clone().detach().numpy()  # no need to transpose
#                 # u_train = u_step.clone().detach().numpy()
#                 # u_in = self.xhat_data[q-1, [0]]   # ==y_out
#                 u_in = np.copy(y_nn)
#                 self.factor.forward(y[q] - y_nn, u_in)
#                 # self.y_pem[q, :] = np.copy(self.factor.Xhat[0, 0])
#                 self.y_pem.append([self.factor.Xhat[0, 0], q])
#                 self.y_pem0.append([None, q])
#                 self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                 y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                 self.xhat_data[q, :] = y_out
#                 x_step = torch.tensor(y_out, dtype=torch.float32)  # !update input to NN !
#
#                 q = q + 1
#
#             if self.update == 3:  # case 1 with threshold, # discard
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt
#                 self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect output of NN
#                 match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                 # print(f'R2 is {match} at data {q}')
#                 while match < self.threshold1 and q < self.N:
#                     self.correction.append(q)
#                     print('evolute at ', q)
#                     u_step = u[q]
#                     dx = self.model(x_step, u_step)
#                     x_step = x_step + dx * self.dt
#                     y_nn = x_step[:, 0].clone().detach().numpy()
#                     self.factor.forward(y[q], y_nn)
#                     self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                     self.xhat_data[q, :] = self.factor.Xhat_data[:, 0]
#                     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#
#                     if match > self.threshold2:
#                         self.stop.append(q)
#                         print(f'evolute finish at {q}, with R2= {match}')
#
#                         break
#                     q = q + 1
#                 q = q + 1
#
#             if self.update == 4:  # case 2 with threshold, resting PEM is also added, can use case 5!! not finished!
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt
#                 #-----
#                 y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                 self.xhat_data[q, :] = y_out
#                 x_step = torch.tensor(y_out, dtype=torch.float32)
#                 #-----
#
#                 self.y_pem0.append([self.factor.Xhat[0, 0], q])
#                 self.y_pem.append([None, q])
#                 self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])  #
#                 match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                 # self.y_pem.append(self.factor.Xhat[0, 0])
#                 if match < self.threshold1:
#                     self.correction.append(q)
#                     print(f'update at {q}, with R2= {match}')
#                     while q < self.N:
#                         u_step = u[q]
#                         dx = self.model(x_step, u_step)
#                         x_step = x_step + dx * self.dt
#                         y_nn = x_step[:, 0].clone().detach().numpy()
#                         # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
#                         u_in = y_nn
#                         # self.factor.pem_rest(y[q] - y_nn, u_in, on=True)
#                         u_in = u_step[:, 0].clone().detach().numpy()
#                         self.factor.pem_rest(y[q]- y_nn, u_in, on=True)
#
#                         # self.y_pem[q, :] = np.copy(self.factor.Xhat[0, 0])  # adding
#                         self.y_pem.append([self.factor.Xhat[0, 0], q])
#                         self.y_pem0.append([None, q])
#                         # self.y_pem[q] = np.copy(self.factor.Xhat[0, 0])
#
#                         self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                         y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                         self.xhat_data[q, :] = y_out
#                         x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
#                         match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                         if match > self.threshold2:
#                             self.stop.append(q)
#                             # print(f'finish at  {q}, with R2= {match}')
#                             self.factor.pem_rest(y[q] - y_nn, u_in, on=False)#
#                             # self.factor.pem_rest(y[q] - y_nn, u_in, on=True)
#
#                             break
#                         q = q + 1
#
#
#                 q = q + 1
#
#             if self.update == 5:  # case 4 with threshold, not adding resting PEM # use this!!!!
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt
#                 self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
#                 self.err[q] = y[q] - x_step[0, 0].clone().detach().numpy()
#                 # --------------------------------------------------------
#                 # y_nn = x_step[:, 0].clone().detach().numpy()
#                 # # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
#                 # u_in = y_nn
#                 # self.factor.pem_rest(y[q] - y_nn, u_in, on=False)
#                 # y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                 # self.xhat_data[q, :] = y_out  # collect
#                 # x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
#                 # --------------------------------------------------------
#                 # self.y_pem0[q, :] = np.copy(self.factor.Xhat[0, 0])  ## different color
#                 self.y_pem0.append([self.factor.Xhat[0, 0], q])
#                 self.y_pem.append([None, q])
#                 # self.y_pem0[q] = np.copy(self.factor.Xhat[0, 0])
#
#                 self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                 match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                 # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
#
#                 # if q > self.sensitivity:
#                 #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                 # if q <= self.sensitivity:
#                 #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
#
#                 self.r2[q] = match
#                 if match < self.threshold1:
#                     print(f'update at {q}, with R2= {match}')
#                     self.correction.append([match, q])
#
#                     while q < self.N:
#                         u_step = u[q]
#                         dx = self.model(x_step, u_step)
#                         x_step = x_step + dx * self.dt
#                         y_nn = x_step[:, 0].clone().detach().numpy()
#                         # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
#                         u_in = y_nn
#                         self.factor.pem_rest(y[q] - y_nn, u_in, on=True)
#
#                         # self.y_pem[q, :] = np.copy(self.factor.Xhat[0, 0])  # adding
#                         self.y_pem.append([self.factor.Xhat[0, 0], q])
#                         self.y_pem0.append([None, q])
#                         # self.y_pem[q] = np.copy(self.factor.Xhat[0, 0])
#
#                         self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                         self.err[q] = y[q] - x_step[0, 0].clone().detach().numpy()
#                         y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                         self.xhat_data[q, :] = y_out
#                         x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
#                         match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                         # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
#
#                         # if q > self.sensitivity:
#                         #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                         # if q <= self.sensitivity:
#                         #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
#                         self.r2[q] = match
#                         if match > self.threshold2:
#                             self.stop.append([match, q])
#                             print(f'finish at  {q}, with R2= {match}')
#                             self.factor.pem_rest(y[q] - y_nn, u_in, on=False)
#                             break
#                         q = q + 1
#                 q = q + 1
#
#             if self.update == 7:  # case 4 with threshold, not adding resting PEM
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt + self.alter
#                 self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
#                 # --------------------------------------------------------
#                 # y_nn = x_step[:, 0].clone().detach().numpy()
#                 # # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
#                 # u_in = y_nn
#                 # self.factor.pem_rest(y[q] - y_nn, u_in, on=False)
#                 # y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                 # self.xhat_data[q, :] = y_out  # collect
#                 # x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
#                 # --------------------------------------------------------
#                 # self.y_pem0[q, :] = np.copy(self.factor.Xhat[0, 0])  ## different color
#                 self.y_pem0.append([self.factor.Xhat[0, 0], q])
#                 self.y_pem.append([None, q])
#                 # self.y_pem0[q] = np.copy(self.factor.Xhat[0, 0])
#
#                 self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                 if q > self.sensitivity:
#                     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                 if q <= self.sensitivity:
#                     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
#
#                 self.r2[q] = match
#                 if match < self.threshold1:
#                     print(f'update at {q}, with R2= {match}')
#                     self.correction.append(q)
#                     while q < self.N:
#                         u_step = u[q]
#                         dx = self.model(x_step, u_step)
#                         x_step = x_step + dx * self.dt + self.alter
#                         y_nn = x_step[:, 0].clone().detach().numpy()
#                         # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
#                         u_in = y_nn
#                         self.factor.pem_rest(y[q] - y_nn, u_in, on=True)
#
#                         # self.y_pem[q, :] = np.copy(self.factor.Xhat[0, 0])  # adding
#                         self.y_pem.append([self.factor.Xhat[0, 0], q])
#                         self.y_pem0.append([None, q])
#                         # self.y_pem[q] = np.copy(self.factor.Xhat[0, 0])
#
#                         self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                         y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                         self.xhat_data[q, :] = y_out
#                         x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
#                         match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                         # if q > self.sensitivity:
#                         #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
#                         # if q <= self.sensitivity:
#                         #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
#                         self.r2[q] = match
#                         if match > self.threshold2:
#                             self.alter[0, 0] = self.factor.Xhat[0, 0]
#                             self.alter[0, 1] = self.factor.Xhat[1, 0]
#                             self.stop.append(q)
#                             print(f'finish at  {q}, with R2= {match}')
#                             self.factor.pem_rest(y[q] - y_nn, u_in, on=False)
#                             break
#                         q = q + 1
#                 q = q + 1
#
#             if self.update == 6:  # same as 5, only for tank, y = x1
#                 u_step = u[q]
#                 dx = self.model(x_step, u_step)
#                 x_step = x_step + dx * self.dt
#                 self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
#
#                 # --------------------------------------------------------
#                 # y_nn = x_step[:, 0].clone().detach().numpy()
#                 # # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
#                 # u_in = y_nn
#                 # self.factor.pem_rest(y[q] - y_nn, u_in, on=False)
#                 # y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                 # self.xhat_data[q, :] = y_out  # collect
#                 # x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
#                 # --------------------------------------------------------
#                 # self.y_pem0[q, :] = np.copy(self.factor.Xhat[0, 0])  ## different color
#                 self.y_pem0.append([self.factor.Xhat[1, 0], q])
#                 self.y_pem.append([None, q])
#                 # self.y_pem0[q] = np.copy(self.factor.Xhat[0, 0])
#
#                 self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                 match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 1])
#                 if match < self.threshold1:
#
#                     print(f'update at {q}, with R2= {match}')
#                     self.correction.append(q)
#                     while q < self.N:
#                         u_step = u[q]
#                         dx = self.model(x_step, u_step)
#                         x_step = x_step + dx * self.dt
#                         y_nn = x_step[:, 1].clone().detach().numpy()
#                         # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
#                         u_in = y_nn
#                         self.factor.pem_rest(y[q] - y_nn, u_in, on=True)
#
#                         # self.y_pem[q, :] = np.copy(self.factor.Xhat[0, 0])  # adding
#                         self.y_pem.append([self.factor.Xhat[1, 0], q])
#                         self.y_pem0.append([None, q])
#                         # self.y_pem[q] = np.copy(self.factor.Xhat[0, 0])
#
#                         self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
#                         y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
#                         self.xhat_data[q, :] = y_out
#                         x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
#                         match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 1])
#                         if match > self.threshold2:
#                             self.stop.append(q)
#                             print(f'finish at  {q}, with R2= {match}')
#                             self.factor.pem_rest(y[q] - y_nn, u_in, on=False)
#                             break
#                         q = q + 1
#                 q = q + 1
#
#         return self.xhat_data


#    ---->>>>> test -----
#
# ----  >>>>>>>>>works>>>>>>>>----------------------


class ForwardEulerPEM(nn.Module):  # use steps or R2 as switch

    def __init__(self, model,
                 factor,
                 dt, N, update, threshold1=0, threshold2=0,
                 sensitivity=600, train=2000):  #sensitivity=100

        super(ForwardEulerPEM, self).__init__()
        self.factor = factor
        self.model = model
        self.dt = dt
        self.N = N

        self.update = update  # choose case

        self.train = train
        self.threshold1 = threshold1  # start update
        self.threshold2 = threshold2  # stop update
        self.sensitivity = sensitivity  # an sequence to monitor R2
        self.stop = []
        self.correction = []
        self.xhat_data = np.zeros((N, 2))


    def forward(self, x0: torch.Tensor, u: torch.Tensor, y):
        x_step = x0
        self.Thehat = np.zeros((self.N, 6))

        self.y_pem = []
        self.y_pem0 = []
        self.r2 = np.zeros(self.N)
        self.alter = torch.zeros(1, 2)
        self.err = np.zeros(self.N)  # |y-yhat|
        # ---------------
        self.on = []
        self.check = []
        x_mul = torch.ones(1, 2)
        # ------------------
        q = 0
        while q < self.N:
            # not updating, no PEM
            if self.update == 0:
                # simple forward Euler
                u_step = u[q]
                dx = self.model(x_step, u_step)
                x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()
                # self.match = R2(y[q - self.sensitivity:q], self.xhat_data[q - self.sensitivity:q, 0])
                # print(f'R2 is {self.match} at data {q}')
                q = q + 1
            # update non-stop:
            if self.update == 1:  # with bar_x in x_step
                u_step = u[q]
                dx = self.model(x_step, u_step)
                x_step = x_step + dx * self.dt #+ torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)
                # self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect output of NN
                y_nn = x_step[:, 0].clone().detach().numpy()
                u_in = y_nn
                self.factor.pem_one(y[q] - y_nn, u_in, on=True)


                x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                self.xhat_data[q, :] = x_out
                x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
                match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                q = q + 1
            # PEM running from beginning, use steps as switch
            if self.update == 2:
                u_step = u[q]
                dx = self.model(x_step, u_step)
                x_step = x_step + dx * self.dt
                y_nn = x_step[:, 0].clone().detach().numpy()
                u_in = y_nn * 0
                # if q <= self.train or q % self.step == 0:
                if q <= self.train or all(np.remainder(q, self.step)) == 0:
                    self.factor.pem_one(y[q] - y_nn, u_in, on=True)
                    self.on.append(q)
                if q > self.train:
                    self.factor.pem_one(y[q] - y_nn, u_in, on=False)
                x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]  # must have [:, 0], from 2x1 to 1x2
                self.xhat_data[q, :] = x_out
                x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
                match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                q = q + 1
            # not finished, PEM running from beginning, use steps as switch, use multiplication
            if self.update == 3:
                u_step = u[q]
                dx = self.model(x_step, u_step)  # * x_mul
                x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[:, 0].clone().detach().numpy()
                y_nn = x_step[:, 0].clone().detach().numpy()
                u_in = y_nn  # [0] #
                if q <= self.train or all(np.remainder(q, self.step)) == 0:  #
                    self.factor.pem_one(y[q] - y_nn, self.param[q], on=True)
                    # self.factor.pem_one(y[q], u_in, on=True)
                    # x_out = self.factor.Xhat

                if q > self.train:
                    self.factor.pem_one(y[q] - y_nn, self.param[q], on=False)
                    # self.factor.pem_one(y[q], u_in, on=False)
                    # x_out = self.factor.Xhat
                    # x_out = x_step.clone().detach().numpy().T
                print(q)
                # self.check.append(self.factor.Xhat)
                # x_out = np.dot(self.factor.Ahat, x_step.clone().detach().numpy().T)  #

                x_out = self.factor.Xhat * x_step.clone().detach().numpy().T
                # x_out = np.dot(self.factor.Ahat, x_step.clone().detach().numpy().T)+self.factor.Bhat*y_nn
                # x_step = torch.tensor(self.factor.Xhat.T, dtype=torch.float32)
                # x_mul = torch.tensor(self.factor.Xhat.T, dtype=torch.float32)
                # x_out = x_step.clone().detach().numpy()
                self.xhat_data[q, :] = x_out[:, 0]
                x_out = x_out.T
                # self.xhat_data[q, :] = x_step.clone().detach().numpy()
                x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
                match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                q = q + 1
            if self.update == 4: #move pem to (dx+pem)*dt, copied from 7, use self.train to stop PEM, not R2
                u_step = u[q]
                # print("alter=", self.alter)
                dx = self.model(x_step, u_step)#+ self.alter
                #
                x_step = x_step + dx * self.dt #+ self.alter
                # x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect

                self.y_pem0.append([self.factor.Xhat[0, 0], q])
                self.y_pem.append([None, q])

                if q > self.sensitivity:
                    match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                if q <= self.sensitivity:
                    match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                self.r2[q] = match
                # for q in range(self.train):
                while q < self.train:
                        u_step = u[q]
                        dx = self.model(x_step, u_step)+ torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)
                        x_step = x_step + dx * self.dt
                        y_nn = x_step[:, 0].clone().detach().numpy()

                        self.factor.pem_one(y[q] - y_nn, y_nn, on=True)

                        self.y_pem.append([self.factor.Xhat[0, 0], q])
                        self.y_pem0.append([None, q])

                        self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                        x_out = x_step.clone().detach().numpy()
                        self.xhat_data[q, :] = x_out
                        # x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
                        # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])

                        # if q > self.sensitivity:
                        #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        # if q <= self.sensitivity:
                        #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                        self.r2[q] = match
                        self.alter = torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)

                        # if q > self.train:
                        #     # print("alter stop=", self.alter)
                        #
                        #     # self.alter[0, 0] = self.factor.Xhat[0, 0]
                        #     # self.alter[0, 1] = self.factor.Xhat[1, 0]
                        #     # self.alter = torch.tensor(self.factor.pem_one(y[q] - y_nn, u_in, on=False).T, dtype=torch.float32)
                        #     self.stop.append(q)
                        #     print(f'finish at  {q}, with R2= {match}')
                        #     break
                        q = q + 1
                q = q + 1
            if self.update == 8: # xtep = x_step+pem, outside of dx, copied from 5, use self.train to stop PEM, not R2, not added when stop, now add
                u_step = u[q]
                # print("alter=", self.alter)
                dx = self.model(x_step, u_step)#+ self.alter
                #
                x_step = x_step + dx * self.dt + torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)# not updating pem added
                # print("outside xhat=", self.factor.Xhat[:, 0])
                # x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect

                self.y_pem0.append([self.factor.Xhat[0, 0], q])
                self.y_pem.append([None, q])

                if q > self.sensitivity:
                    match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                if q <= self.sensitivity:
                    match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                self.r2[q] = match
                # for q in range(self.train):
                while q < self.train:
                        u_step = u[q]
                        dx = self.model(x_step, u_step)
                        # x_step = x_step + dx * self.dt + self.alter
                        x_step = x_step + dx * self.dt

                        y_nn = x_step[:, 0].clone().detach().numpy()

                        self.factor.pem_one(y[q] - y_nn, y_nn, on=True)

                        self.y_pem.append([self.factor.Xhat[0, 0], q])
                        self.y_pem0.append([None, q])
                        self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                        x_out = x_step.clone().detach().numpy()+ self.factor.Xhat[:, 0]
                        self.xhat_data[q, :] = x_out
                        x_step = torch.tensor(x_out, dtype=torch.float32)  # don't delete this ! update input to NN !
                        # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])

                        # if q > self.sensitivity:
                        #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        # if q <= self.sensitivity:
                        #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                        self.r2[q] = match
                        # self.alter = torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)

                        # if q > self.train:
                        #     # print("alter stop=", self.alter)
                        #
                        #     # self.alter[0, 0] = self.factor.Xhat[0, 0]
                        #     # self.alter[0, 1] = self.factor.Xhat[1, 0]
                        #     # self.alter = torch.tensor(self.factor.pem_one(y[q] - y_nn, u_in, on=False).T, dtype=torch.float32)
                        #     self.stop.append(q)
                        #     print(f'finish at  {q}, with R2= {match}')
                        #     break
                        q = q + 1
                y_nn = x_step[:, 0].clone().detach().numpy()
                self.factor.pem_one(0, y_nn, on=False)  # for pem n-step ahead  y[q]*0 - y_nn

                q = q + 1

            # update with threshold,  adding resting PEM !! # use this!!

            if self.update == 5:
                u_step = u[q]
                dx = self.model(x_step, u_step)
                # y_nn = x_step[:, 0].clone().detach().numpy()
                # self.factor.pem_one(y[q] * 0 - y_nn, y_nn, on=False)  # for pem n-step ahead
                x_step = x_step + dx * self.dt + torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)# not updating pem added
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
                self.err[q] = y[q] - x_step[0, 0].clone().detach().numpy()
                # # --------------------------------------------------------
                # if q < 1000:
                #     y_nn = x_step[:, 0].clone().detach().numpy()
                #     u_in = y_nn
                #     self.factor.pem_one(y[q] - y_nn, u_in, on=True)
                #     x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                #     self.xhat_data[q, :] = x_out  # collect
                #     x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
                #
                # # --------------------------------------------------------
                self.y_pem0.append([self.factor.Xhat[0, 0], q])
                self.y_pem.append([None, q])


                self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0]) # check the dimension before use
                # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])

                # if q > self.sensitivity:
                #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                # if q <= self.sensitivity:
                #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                match = round(match, 3)
                self.r2[q] = match
                if match < self.threshold1:
                    print(f'update at {q}, with R2= {match}')
                    self.correction.append([match, q])
                    while q < self.N:
                        u_step = u[q]
                        dx = self.model(x_step, u_step)
                        x_step = x_step + dx * self.dt
                        y_nn = x_step[:, 0].clone().detach().numpy()

                        self.factor.pem_one(y[q] - y_nn, y_nn, on=True)

                        self.y_pem.append([self.factor.Xhat[0, 0], q])
                        self.y_pem0.append([None, q])

                        self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                        self.err[q] = y[q] - x_step[0, 0].clone().detach().numpy()
                        x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                        self.xhat_data[q, :] = x_out
                        x_step = torch.tensor(x_out, dtype=torch.float32)  # don't delete this ! update input to NN !
                        match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])

                        # if q > self.sensitivity:
                        #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        # if q <= self.sensitivity:
                        #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                        match = round(match, 3)
                        self.r2[q] = match
                        if match > self.threshold2:
                            self.stop.append([match, q])
                            print(f'finish at  {q}, with R2= {match}')

                            break
                        q = q + 1


                y_nn = x_step[:, 0].clone().detach().numpy()
                # print(q)
                self.factor.pem_one(y[q]*0 - y_nn, y_nn, on=False)  # for pem n-step ahead
                q = q + 1

            if self.update == 9: #move pem to (dx+pem)*dt, copied from 7, some fixing, some error
                u_step = u[q]
                dx = self.model(x_step, u_step) + torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)# not updating pem added
                # x_step = x_step + dx * self.dt
                x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
                # --------------------------------------------------------
                # if q < 1000:
                #     y_nn = x_step[:, 0].clone().detach().numpy()
                #     u_in = y_nn
                #     self.factor.pem_one(y[q] - y_nn, u_in, on=True)
                #     x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                #     self.xhat_data[q, :] = x_out  # collect
                #     x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
                # --------------------------------------------------------
                self.y_pem0.append([self.factor.Xhat[0, 0], q])
                self.y_pem.append([None, q])

                self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                if q > self.sensitivity:
                    match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                if q <= self.sensitivity:
                    match = R2(y[0:q, 0], self.xhat_data[0:q, 0])

                self.r2[q] = match
                if match < self.threshold1:
                    print(f'update at {q}, with R2= {match}')
                    self.correction.append(q)
                    while q < self.N:
                        u_step = u[q]
                        dx = self.model(x_step, u_step) + torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)
                        # x_step = x_step + dx * self.dt + self.alter
                        x_step = x_step + dx * self.dt
                        y_nn = x_step[:, 0].clone().detach().numpy()

                        self.factor.pem_one(y[q] - y_nn, y_nn, on=True)

                        self.y_pem.append([self.factor.Xhat[0, 0], q])
                        self.y_pem0.append([None, q])

                        self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                        y_out = x_step.clone().detach().numpy()
                        self.xhat_data[q, :] = y_out
                        x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
                        # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])

                        # if q > self.sensitivity:
                        #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        # if q <= self.sensitivity:
                        #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                        self.r2[q] = match
                        if match > self.threshold2:
                            # self.alter[0, 0] = self.factor.Xhat[0, 0]
                            # self.alter[0, 1] = self.factor.Xhat[1, 0]
                            # self.alter = torch.tensor(self.factor.pem_one(y[q] - y_nn, u_in, on=False).T, dtype=torch.float32)
                            self.stop.append(q)
                            print(f'finish at  {q}, with R2= {match}')
                            break

                        q = q + 1
                q = q + 1
                # y_nn = x_step[:, 0].clone().detach().numpy()
                # self.factor.pem_one(y[q] - y_nn, y_nn, on=False)  # for pem n-step ahead


            # test use threshold, alter added, better explained than  5
            if self.update == 7: #move pem to (dx+pem)*dt
                u_step = u[q]
                dx = self.model(x_step, u_step) + self.alter
                # x_step = x_step + dx * self.dt + self.alter
                x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
                # --------------------------------------------------------
                # if q < 1000:
                #     y_nn = x_step[:, 0].clone().detach().numpy()
                #     u_in = y_nn
                #     self.factor.pem_one(y[q] - y_nn, u_in, on=True)
                #     x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                #     self.xhat_data[q, :] = x_out  # collect
                #     x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
                # --------------------------------------------------------
                self.y_pem0.append([self.factor.Xhat[0, 0], q])
                self.y_pem.append([None, q])

                self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                if q > self.sensitivity:
                    match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                if q <= self.sensitivity:
                    match = R2(y[0:q, 0], self.xhat_data[0:q, 0])

                self.r2[q] = match
                if match < self.threshold1:
                    print(f'update at {q}, with R2= {match}')
                    self.correction.append(q)
                    while q < self.N:
                        u_step = u[q]
                        dx = self.model(x_step, u_step) + torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)
                        # x_step = x_step + dx * self.dt + self.alter
                        x_step = x_step + dx * self.dt
                        y_nn = x_step[:, 0].clone().detach().numpy()
                        u_in = y_nn
                        self.factor.pem_one(y[q] - y_nn, u_in, on=True)

                        self.y_pem.append([self.factor.Xhat[0, 0], q])
                        self.y_pem0.append([None, q])

                        self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                        y_out = x_step.clone().detach().numpy()
                        self.xhat_data[q, :] = y_out
                        x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
                        # match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])

                        # if q > self.sensitivity:
                        #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
                        # if q <= self.sensitivity:
                        #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
                        self.r2[q] = match
                        self.alter =torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)
                        if match > self.threshold2:
                            # self.alter[0, 0] = self.factor.Xhat[0, 0]
                            # self.alter[0, 1] = self.factor.Xhat[1, 0]
                            #  torch.tensor(self.factor.pem_one(y[q] - y_nn, u_in, on=False).T, dtype=torch.float32)
                            # self.alter = torch.tensor(self.factor.pem_one(y[q] - y_nn, u_in, on=False), dtype=torch.float32)

                            self.stop.append(q)
                            print(f'finish at  {q}, with R2= {match}')
                            break
                        q = q + 1
                q = q + 1
            # same as 5, only for tank, y = x1
            if self.update == 6: # for tanks, with regulator added
                u_step = u[q]
                dx = self.model(x_step, u_step)
                x_step = x_step + dx * self.dt #+ torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect

                # --------------------------------------------------------
                # y_nn = x_step[:, 0].clone().detach().numpy()
                # # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
                # u_in = y_nn
                # self.factor.pem_one(y[q] - y_nn, u_in, on=False)
                # y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                # self.xhat_data[q, :] = y_out  # collect
                # x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
                # --------------------------------------------------------
                # self.y_pem0[q, :] = np.copy(self.factor.Xhat[0, 0])  ## different color
                self.y_pem0.append([self.factor.Xhat[1, 0], q])
                self.y_pem.append([None, q])
                # self.y_pem0[q] = np.copy(self.factor.Xhat[0, 0])

                self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])

                match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 1])
                if match < self.threshold1:

                    print(f'update at {q}, with R2= {match}')
                    self.correction.append(q)
                    while q < self.N:
                        u_step = u[q]
                        dx = self.model(x_step, u_step)
                        x_step = x_step + dx * self.dt #+ torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)

                        y_nn = x_step[:, 1].clone().detach().numpy()
                        # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
                        u_in = y_nn
                        self.factor.pem_one(y[q] - y_nn, u_in, on=True)

                        # self.y_pem[q, :] = np.copy(self.factor.Xhat[0, 0])  # adding
                        self.y_pem.append([self.factor.Xhat[1, 0], q])
                        self.y_pem0.append([None, q])
                        # self.y_pem[q] = np.copy(self.factor.Xhat[0, 0])
                        self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                        y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                        # print('shape,', y_out.shape)
                        self.xhat_data[q, :] = y_out
                        x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !


                        match = R2(y[q - self.sensitivity:q, 0,0], self.xhat_data[q - self.sensitivity:q, 1])
                        if match > self.threshold2:
                            self.stop.append(q)
                            print(f'finish at  {q}, with R2= {match}')
                            self.factor.pem_one(y[q] - y_nn, u_in, on=False)
                            break
                        q = q + 1
                q = q + 1


            # if self.update == 4: # y1+y2, linear update seperately??
            #     u_step = u[q]
            #     dx = self.model(x_step, u_step)
            #     x_step = x_step + dx * self.dt
            #     # self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
            #     self.err[q] = y[q] - x_step[0, 0].clone().detach().numpy()
            #     # --------------------------------------------------------
            #     if q < 1000:
            #         y_nn = x_step[:, 0].clone().detach().numpy()
            #         u_in = y_nn
            #         self.factor.pem_one(y[q] - y_nn, u_in, on=True)
            #         x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
            #         self.xhat_data[q, :] = x_out  # collect
            #         x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
            #
            #     # --------------------------------------------------------
            #     self.y_pem0.append([self.factor.Xhat[0, 0], q])
            #     self.y_pem.append([None, q])
            #
            #     self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
            #     # match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0]) # check the dimension before use
            #     match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
            #
            #     # if q > self.sensitivity:
            #     #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
            #     # if q <= self.sensitivity:
            #     #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
            #
            #     self.r2[q] = match
            #     if match < self.threshold1:
            #         print(f'update at {q}, with R2= {match}')
            #         self.correction.append([match, q])
            #         while q < self.N:
            #             u_step = u[q]
            #             dx = self.model(x_step, u_step)
            #             x_step = x_step + dx * self.dt
            #             y_nn = x_step[:, 0].clone().detach().numpy()
            #             u_in = y_nn
            #             self.factor.pem_one(y[q] - y_nn, u_in, on=True)
            #
            #             self.y_pem.append([self.factor.Xhat[0, 0], q])
            #             self.y_pem0.append([None, q])
            #
            #             self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
            #             self.err[q] = y[q] - x_step[0, 0].clone().detach().numpy()
            #             x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
            #             self.xhat_data[q, :] = x_out
            #             x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !
            #             # match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
            #             match = R2(y[q - self.sensitivity:q, 0, 0], self.xhat_data[q - self.sensitivity:q, 0])
            #
            #             # if q > self.sensitivity:
            #             #     match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 0])
            #             # if q <= self.sensitivity:
            #             #     match = R2(y[0:q, 0], self.xhat_data[0:q, 0])
            #             self.r2[q] = match
            #             if match > self.threshold2:
            #                 self.stop.append([match, q])
            #                 print(f'finish at  {q}, with R2= {match}')
            #                 break
            #             q = q + 1
            #     q = q + 1
        return self.xhat_data


class ForwardEulerPEM_ahead(nn.Module):  # same as ForwardEulerPEM, but with n step ahead prediction

    def __init__(self, model,
                 factor,
                 dt, N, update, ahead_step):

        super(ForwardEulerPEM_ahead, self).__init__()
        self.factor = factor
        self.model = model
        self.dt = dt
        self.N = N
        self.update = update  # choose case
        self.step = ahead_step
        self.xhat_data = np.zeros((N, 2))
        self.E_data = []
        self.predict_data = []

    def forward(self, x0: torch.Tensor, u: torch.Tensor, y, pre_ahead=True):
        x_step = x0

        q = 0
        while q < self.N:

            if self.update == 8: #move pem to (dx+pem)*dt
                u_step = u[q]
                dx = self.model(x_step, u_step) + torch.tensor(self.factor.Xhat[:, 0], dtype=torch.float32)
                x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect
                y_nn = x_step[:, 0].clone().detach().numpy()
                u_in = y_nn
                self.factor.pem_one(y[q] - y_nn, u_in, on=True)

                # --------------------------------------------------------
                if pre_ahead:
                    predict = []
                    if q < self.N - self.step:  # 1000 <
                        xhat_step = x_step
                        for z in range(self.step):
                            u_step = u[q + z + 1]
                            dx = self.model(xhat_step, u_step) + torch.tensor(self.factor.Xhat[:, 0],
                                                                              dtype=torch.float32)
                            xhat_step_new = xhat_step + dx * self.dt
                            y_nn = xhat_step_new[:, 0].clone().detach().numpy()
                            u_in = y_nn
                            self.factor.pem_one(y[q] - y_nn, u_in, on=False)  #
                            predict.append(xhat_step_new[:, 0].clone().detach().numpy())
                            xhat_step = xhat_step_new

                            # self.E = mse(y[q:q + self.step], predict)
                        self.E = mse(y[q + self.step], predict[-1])
                        # print('q=', q)
                        # print('E=', self.E)
                        self.E_data.append(self.E)
                        self.predict_data.append(predict[self.step-1])

                y_out = x_step.clone().detach().numpy()

                self.xhat_data[q, :] = y_out
                x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !

            q = q + 1

        return self.xhat_data



class ForwardEulerPEM_mimo(nn.Module):
    """
    PEM mimo, non-stop
    """
    def __init__(self, model, factor, dt, N, n, m, u, update=1):
        super(ForwardEulerPEM_mimo, self).__init__()
        self.factor = factor  # pem_mimo
        self.model = model  # mechanical system
        self.dt = dt
        self.N = N
        self.n = n
        self.m = m
        self.u=u

        self.update = update  # choose case
        self.xhat_data = np.zeros((N, n))


    def forward(self, x0: torch.Tensor, u: torch.Tensor, y):
        x_step = x0
        C = np.eye(self.m, self.n)
        # ------------------
        q = 0
        while q < self.N:
            # not updating, no PEM, pure NN
            if self.update == 0:
                # simple forward Euler
                u_step = u[q]
                dx = self.model(x_step, u_step)
                x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()
                q = q + 1
            # update non-stop:
            if self.update == 1:
                u_step = u[q]
                dx = self.model(x_step, u_step)
                x_step = x_step + dx * self.dt
                x_nn = x_step.clone().detach().numpy()
                y_nn = (C@x_nn.T).T  #[:, [0]]
                self.factor.forward(y[q, :] - y_nn, y_nn)  # x_nn.T
                x_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                self.xhat_data[q, :] = x_out
                x_step = torch.tensor(x_out, dtype=torch.float32)  # ! update input to NN !

                q = q + 1


            # same as 5, only for tank, y = x1, not finished?
            if self.update == 6:
                u_step = u[q]
                dx = self.model(x_step, u_step)
                x_step = x_step + dx * self.dt
                self.xhat_data[q, :] = x_step[0, :].clone().detach().numpy()  # collect

                # --------------------------------------------------------
                # y_nn = x_step[:, 0].clone().detach().numpy()
                # # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
                # u_in = y_nn
                # self.factor.pem_one(y[q] - y_nn, u_in, on=False)
                # y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                # self.xhat_data[q, :] = y_out  # collect
                # x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
                # --------------------------------------------------------
                # self.y_pem0[q, :] = np.copy(self.factor.Xhat[0, 0])  ## different color
                self.y_pem0.append([self.factor.Xhat[1, 0], q])
                self.y_pem.append([None, q])
                # self.y_pem0[q] = np.copy(self.factor.Xhat[0, 0])

                self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 1])
                if match < self.threshold1:

                    print(f'update at {q}, with R2= {match}')
                    self.correction.append(q)
                    while q < self.N:
                        u_step = u[q]
                        dx = self.model(x_step, u_step)
                        x_step = x_step + dx * self.dt
                        y_nn = x_step[:, 1].clone().detach().numpy()
                        # u_in = self.xhat_data[q - 1, [0]]  # ==y_out
                        u_in = y_nn
                        self.factor.pem_one(y[q] - y_nn, u_in, on=True)

                        # self.y_pem[q, :] = np.copy(self.factor.Xhat[0, 0])  # adding
                        self.y_pem.append([self.factor.Xhat[1, 0], q])
                        self.y_pem0.append([None, q])
                        # self.y_pem[q] = np.copy(self.factor.Xhat[0, 0])

                        self.Thehat[q, :] = np.copy(self.factor.Thehat[:, 0])
                        y_out = x_step.clone().detach().numpy() + self.factor.Xhat[:, 0]
                        self.xhat_data[q, :] = y_out
                        x_step = torch.tensor(y_out, dtype=torch.float32)  # ! update input to NN !
                        match = R2(y[q - self.sensitivity:q, 0], self.xhat_data[q - self.sensitivity:q, 1])
                        if match > self.threshold2:
                            self.stop.append(q)
                            print(f'finish at  {q}, with R2= {match}')
                            self.factor.pem_one(y[q] - y_nn, u_in, on=False)
                            break
                        q = q + 1
                q = q + 1

        return self.xhat_data


