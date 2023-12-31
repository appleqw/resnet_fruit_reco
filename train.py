import argparse
import glob
import math
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import pylab as p
import timm
from PIL import Image


from timm.utils import accuracy, misc

import torch.utils.data
import torchvision.transforms
from timm.utils import NativeScaler

from torch.utils.tensorboard import SummaryWriter

device=torch.device("cuda")
def evaluate(data_loader,model,device):
    criterion=torch.nn.CrossEntropyLoss()

    metric_logger=misc.MetricLogger(delimiter=" ")
    header='Test'

    model.eval()

    for batch in metric_logger.log_every(data_loader,10,header):
        images=batch[0]
        target=batch[-1]
        images=images.to(device,non_blacking=True)
        target=target.to(device,non_blacking=True)

        with torch.no_grad():
            output=model(images)
            loss=criterion(output,target)

        output=torch.nn.functional.softmax(output,dim=1)
        acc1,acc5=accuracy(output,target,topk=(1,5))

        bach_size=images.shape[0]
        metric_logger.update(loss=loss.item())
        metric_logger.meters['acc1'].update(acc1.item(),n=bach_size)
        metric_logger.meters['acc5'].update(acc5.item(),n=bach_size)
    #gather the stats from all process
    metric_logger.synchronize_between_processes()
    print('* Acc@1{top1.global_avg:.3f} Acc@5{top5.global_avg:.3f}loss{losses.global_avg:.3f}'
          .format(top1=metric_logger.acc1,top5=metric_logger.acc5,losses=metric_logger.loss))

    return {k: meter.global_avg for k ,meter in metric_logger.meters.item()}

def train_one_epoch(model:torch.nn.Module,criterion:torch.nn.Module,
                    data_loader:Iterable,optimizer:torch.optim.Optimizer,
                    device:torch.device,epoch :int, loss_scaler,max_norm:float =0,
                    log_writer=None,
                    args=None):
    model.train(True)

    print_freq=2

    accum_iter=args.accum_iter

    if log_writer is not None:
        print('log_dir:{}'.format(log_writer.log_dir))

    for data_iter_step,(samples,targets) in enumerate(data_loader):

        samples=samples.to(device,non_bocking=True)
        targets=targets.to(device,non_bocking=True)

        outputs=model(samples)

        #warmup_lr =args.lr*(min(1.0,epoch/2.))
        warmup_lr=args.lr
        optimizer.param_groups[0]["lr"]=warmup_lr

        loss=criterion(outputs,targets)
        loss/=accum_iter

        loss_scaler(loss,clip_grad=max_norm,
                    parameters=model.parameters(),create_graph=False,
                    update_grad=(data_iter_step+1)%accum_iter==0)

        loss_value=loss.item()
        if(data_iter_step+1)%accum_iter==0:
            optimizer.zero_grad()

        if not math.isfinite(loss_value):
            print('Loss is {},stopping training'.format(loss_value))
            sys.exit(1)

        if log_writer is not None and(data_iter_step+1)%accum_iter ==0:
            epoch_1000x=int((data_iter_step/len(data_loader)+epoch)*1000)
            log_writer.add_scalar('loss',loss_value,epoch_1000x)
            log_writer.add_scalar('lr',warmup_lr,epoch_1000x)
            print(f'Epoch{epoch},Step{data_iter_step},loss:{loss},lr:{warmup_lr}')

def build_transform(is_train,args):
    if is_train:
        #this shoud always dispatch to transforms_imagenet_train
        print("train transform")
        return torchvision.transforms.Compose(
            [
                torchvision.transforms.Resize((args.input_size,args.input_size)),
                torchvision.transforms.RandomHorizontalFlip(),
                torchvision.transforms.RandomVerticalFlip(),
                torchvision.transforms.RandomPerspective(distortion_scale=0.6,p=1.0),
                torchvision.transforms.GaussianBlur(kernel_size=(5,9),sigma=(0.1,5)),
                torchvision.transforms.ToTensor(),
            ]
        )
    else:    #eval tranform
        print("eval transform")
        return torchvision.transforms.Compose(
            [
                torchvision.transforms.Resize((args.input_size,args.input_size)),
                torchvision.transforms.ToTensor(),
            ]
        )

def build_dataset(is_train,args):
    transform=build_transform(is_train,args)
    path=os.path.join(args.root_path,'train' if is_train else 'test')
    dataset=torchvision.datasets.ImageFolder(path,transform=transform)
    info=dataset.find_classes(path)
    print(f"finding classes from {path}:\t{info[0]}")
    print(f'mapping classes from {path} to indexes:\t{info[1]}')

    return dataset

def get_args_parser():
    parser=argparse.ArgumentParser('MAN pre-training',add_help=False)
    parser.add_argument('--batch_size',default=72,type=int,
                        help='Batch size per GPU(effectibe batch size is batch_size)')
    parser.add_argument('--epochs',default=400,type=int)
    parser.add_argument('--accum_iter',default=1,type=int,
                        help='Accum_iter gradient iterations ( for increasing the effective batch size under memory)')

    parser.add_argument('--input_size',default=128,type=int,
                        help='image input size')

    parser.add_argument('--weight_decay',type=float,default=0.0001,
                        help='weight decay  (default:0.05)')

    parser.add_argument('--lr',type=float,metavar='LR',
                        help='learning rate')
    parser.add_argument('--root_path',default='',
                        help='path where to save,empty for no saving')
    parser.add_argument('--output_dir',default='./output_dir_pretrained',
                        help='path where to save,empty for no saving')
    parser.add_argument('--log_dir',default='./output_dir_pretrained',
                        help='path where to tensorboard log')

    parser.add_argument('--resume',default='',
                        help='resume from checkpoint')

    parser.add_argument('--start_epoch',default=0,type=int,metavar='M',
                        help='start epoch')

    parser.add_argument('--num_workers',default=5,type=int)

    parser.add_argument('--pin_mem',action='store_true',
                        help='Pin CPU memory in Dataloader for more effective transfer to GPu')
    parser.add_argument('--no_pin_mem',action='store_false',dest='pin_mem')
    parser.set_defaults(pin_mem=True)

    return parser

def main(args, mode='train', test_image_path=''):
    print(f'{mode} mode ...')
    if mode=='train':
        #构建数据批次
        dataset_train=build_dataset(is_train=True,args=args)
        dataset_val=build_dataset(is_train=False,args=args)

        sampler_train=torch.utils.data.RandomSampler(dataset_train)
        sampler_val=torch.utils.data.SequentialSampler(dataset_val)

        data_loader_train=torch.utils.data.DataLoader(
            dataset_train,sampler=sampler_train,
            batch_size=args.batch_size,
            num_workers=args.num_workers,
            pin_memory=args.pin_mem,
            drop_last=True,
        )

        data_loader_var=torch.utils.data.DataLoader(
            dataset_val,sampler=sampler_val,
            batch_size=args.batch_size,
            num_workers=args.num_workers,
            pin_memory=args.pin_mem,
            drop_last=False
        )

        #构建模型
        model=timm.create_model('resnet18',pretrained=True,drop_rate=0.1,drop_path_rate=0.1)

        n_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad)
        print('number of params (M): %.2f'% (n_parameters)/1e6)

        criterion=torch.nn.CrossEntropyLoss()
        optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay)
        os.makedirs(args.log_dir,exist_ok=True)
        log_writer=SummaryWriter(log_dir=args.log_dir)
        loss_scaler=NativeScaler()

        misc.load_model(args=args,model_without_ddp=model,optimizer=optimizer,loss_scaler=loss_scaler)

        for epoch in range(args.start_rpoch,args.epochs):
            print(f'Epoch {epoch}')
            print(f'length of data_loader_train is {len(data_loader_train)}')

            if epoch%1==0:
                print('Evaluating...')
                model.eval()
                test_stats=evaluate(data_loader_var,model,device)
                print(f'Accuracy of the network on the {len(dataset_val)} test images:{test_stats["acc1"]:.f}%')

                if log_writer is not None:
                    log_writer.add_scalar('perf/test_acc1',test_stats['acc1'],epoch)
                    log_writer.add_scalar('perf/test_acc5',test_stats['acc5'],epoch)
                    log_writer.add_scalar('perf/test_loss',test_stats['loss'],epoch)
                model.train()

            print('Training...')
            train_stats=train_one_epoch(
                model,criterion,data_loader_train,
                optimizer,device,epoch+1,
                loss_scaler,None,
                log_writer=log_writer,
                args=args
            )

            if args.outpput_dir:
                print('Saving checkpoints...')
                misc.save_model(
                    args=args,model=model,model_without_ddp=model,optimizer=optimizer,
                    loss_scaler=loss_scaler,epoch=epoch
                )

    else:
        model=timm.create_model('resnet18',pretrained=True,num_classes=36,drop_rate=0.1,drop_path_rate=0.1)

        class_dict={'apple': 0, 'banana': 1, 'beetroot': 2, 'bell pepper': 3, 'cabbage': 4, 'capsicum': 5, 'carrot': 6, 'cauliflower': 7, 'chilli pepper': 8, 'corn': 9, 'cucumber': 10, 'eggplant': 11, 'garlic': 12, 'ginger': 13, 'grapes': 14, 'jalepeno': 15, 'kiwi': 16, 'lemon': 17, 'lettuce': 18, 'mango': 19, 'onion': 20, 'orange': 21, 'paprika': 22, 'pear': 23, 'peas': 24, 'pineapple': 25, 'pomegranate': 26, 'potato': 27, 'raddish': 28, 'soy beans': 29, 'spinach': 30, 'sweetcorn': 31, 'sweetpotato': 32, 'tomato': 33, 'turnip': 34, 'watermelon': 35}

        n_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad)
        print('number of params (M): %.2f' %(n_parameters/1.e6))

        optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay)
        os.makedirs(args.log_dir,exist_ok=True)
        loss_scaler=NativeScaler()

        misc.load_model(args=args,model_without_ddp=model,optimizer=optimizer,loss_scaler=loss_scaler)
        model.eval()

        image=Image.open(test_image_path).convert('RGB')
        img=image.resize((args.input_szie,args.input_szie),Image.ANTIALIAS)
        image=torchvision.transforms.ToTensor()(image).unsqueeze(0)

        with torch.no_grad():
            output=model(image)


        output=torch.nn.functional.softmax(output,dim=1)
        class_idx=torch.argmax(output,dim=1)[0]
        score=torch.max(output,dim=1)[0][0]
        print(f'image path is {test_image_path}')
        print(f'score is {score.item()},class id is {class_idx.item()},class name is {list(class_dict.keys())[list(class_dict.values()).index(class_idx)]}')
        time.sleep(0.5)

if __name__ == '__main__':
    args=get_args_parser()
    args=args.parse_args()

    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True,exist_ok=True)

    mode='train'
    if mode =='train':
        main(args,mode=mode)
    else:
        images=glob.glob('test')

        for image in images:
            print('\n')
            main(args,mode=mode,test_image_path=image)

