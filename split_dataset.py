
import os
import glob
import random
import shutil
from PIL import Image

#对所有图片进行RGB转换，并且统一调整到一致大小，但不让图片发生变形或扭曲


if __name__ == '__main__':
    test_split_radio=0.05   #对每个类别取5%作为测试集，其他作为训练集
    desired_size=128   #图片缩放后统一大小
    raw_path="./raw"

    #将path路径下有多少类别定位出来
    dirs=glob.glob(os.path.join(raw_path,'*'))
    dirs=[d for d in dirs if os.path.isdir(d)]

    print(f"Totally {len(dirs)} classed:{dirs}")

    for path in dirs:
        #对每个类别进行单独处理
        path=path.split('\\')[-1]    #保留类别名称

        os.makedirs(f"train/{path}",exist_ok=True)
        os.makedirs(f"test/{path}",exist_ok=True)

        files=glob.glob(os.path.join(raw_path,path,'*.jpg'))
        files+=glob.glob(os.path.join(raw_path,path,'*.JPG'))
        files+=glob.glob(os.path.join(raw_path,path,'*.png'))

        random.shuffle(files)   #随机取5%

        boundary=int(len(files)*test_split_radio)   #测试集和训练集的边界

        for i,file in enumerate(files):
            #对文件进行遍历
            img=Image.open(file).convert("RGB") #将图像变为rgb三通道

            old_size=img.size  #old_size[0] is in (width,height) format

            ratio=float(desired_size)/max(old_size)

            new_size=tuple([int(x*ratio) for x in old_size])    #按照原始照片的比例对照片进行缩放

            im=img.resize(new_size,Image.ANTIALIAS) #不会对图片造成模糊

            new_im=Image.new("RGB",(desired_size,desired_size)) #定义一个实例

            new_im.paste(im,((desired_size-new_size[0])//2,
                             (desired_size-new_size[1])//2))

            assert new_im.mode=='RGB'

            if i <=boundary:
                #将图片保存一下
                print(file.split('\\')[-1].split('.')[0])
                new_im.save(os.path.join(f"test/{path}",file.split('\\')[-1].split('.')[0]+'.jpg'))
            else:
                new_im.save(os.path.join(f"train/{path}", file.split('\\')[-1].split('.')[0]+'.jpg'))


    test_files=glob.glob(os.path.join('test','*','*.jpg'))
    train_files=glob.glob(os.path.join('train','*','*.jpg'))

    print(f'Totally {len(train_files)} files for training')
    print(f'Totally {len(test_files)} files for test')

