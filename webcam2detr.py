import torch
import argparse
from models import build_model

import numpy as np
import cv2
import time

from PIL import Image
import matplotlib.pyplot as plt
import torchvision.transforms as T


# COCO classes
CLASSES = [
    'N/A', 'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
    'train', 'truck', 'boat', 'traffic light', 'fire hydrant', 'N/A',
    'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse',
    'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'N/A', 'backpack',
    'umbrella', 'N/A', 'N/A', 'handbag', 'tie', 'suitcase', 'frisbee', 'skis',
    'snowboard', 'sports ball', 'kite', 'baseball bat', 'baseball glove',
    'skateboard', 'surfboard', 'tennis racket', 'bottle', 'N/A', 'wine glass',
    'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich',
    'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake',
    'chair', 'couch', 'potted plant', 'bed', 'N/A', 'dining table', 'N/A',
    'N/A', 'toilet', 'N/A', 'tv', 'laptop', 'mouse', 'remote', 'keyboard',
    'cell phone', 'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'N/A',
    'book', 'clock', 'vase', 'scissors', 'teddy bear', 'hair drier',
    'toothbrush'
]

# colors for visualization
COLORS = [[0.000, 0.447, 0.741], [0.850, 0.325, 0.098], [0.929, 0.694, 0.125],
          [0.494, 0.184, 0.556], [0.466, 0.674, 0.188], [0.301, 0.745, 0.933]]


def get_args_parser():
	parser = argparse.ArgumentParser('Set transformer detector', add_help=False)
	parser.add_argument('--lr', default=1e-4, type=float)
	parser.add_argument('--lr_backbone', default=1e-5, type=float)
	parser.add_argument('--batch_size', default=2, type=int)
	parser.add_argument('--weight_decay', default=1e-4, type=float)
	parser.add_argument('--epochs', default=300, type=int)
	parser.add_argument('--lr_drop', default=200, type=int)
	parser.add_argument('--clip_max_norm', default=0.1, type=float,
		        help='gradient clipping max norm')

	# Model parameters
	parser.add_argument('--frozen_weights', type=str, default=None,
		        help="Path to the pretrained model. If set, only the mask head will be trained")
	# * Backbone
	parser.add_argument('--backbone', default='resnet18', type=str,
		        help="Name of the convolutional backbone to use")
	parser.add_argument('--dilation', action='store_true',
		        help="If true, we replace stride with dilation in the last convolutional block (DC5)")
	parser.add_argument('--position_embedding', default='sine', type=str, choices=('sine', 'learned'),
		        help="Type of positional embedding to use on top of the image features")

	# * Transformer
	parser.add_argument('--enc_layers', default=6, type=int,
		        help="Number of encoding layers in the transformer")
	parser.add_argument('--dec_layers', default=6, type=int,
		        help="Number of decoding layers in the transformer")
	parser.add_argument('--dim_feedforward', default=2048, type=int,
		        help="Intermediate size of the feedforward layers in the transformer blocks")
	parser.add_argument('--hidden_dim', default=256, type=int,
		        help="Size of the embeddings (dimension of the transformer)")
	parser.add_argument('--dropout', default=0.1, type=float,
		        help="Dropout applied in the transformer")
	parser.add_argument('--nheads', default=8, type=int,
		        help="Number of attention heads inside the transformer's attentions")
	parser.add_argument('--num_queries', default=100, type=int,
		        help="Number of query slots")
	parser.add_argument('--pre_norm', action='store_true')

	# * Segmentation
	parser.add_argument('--masks', action='store_true',
		        help="Train segmentation head if the flag is provided")

	# Loss
	parser.add_argument('--no_aux_loss', dest='aux_loss', action='store_false',
		        help="Disables auxiliary decoding losses (loss at each layer)")
	# * Matcher
	parser.add_argument('--set_cost_class', default=1, type=float,
		        help="Class coefficient in the matching cost")
	parser.add_argument('--set_cost_bbox', default=5, type=float,
		        help="L1 box coefficient in the matching cost")
	parser.add_argument('--set_cost_giou', default=2, type=float,
		        help="giou box coefficient in the matching cost")
	# * Loss coefficients
	parser.add_argument('--mask_loss_coef', default=1, type=float)
	parser.add_argument('--dice_loss_coef', default=1, type=float)
	parser.add_argument('--bbox_loss_coef', default=5, type=float)
	parser.add_argument('--giou_loss_coef', default=2, type=float)
	parser.add_argument('--eos_coef', default=0.1, type=float,
		        help="Relative classification weight of the no-object class")

	# dataset parameters
	parser.add_argument('--dataset_file', default='coco')
	parser.add_argument('--coco_path', type=str)
	parser.add_argument('--coco_panoptic_path', type=str)
	parser.add_argument('--remove_difficult', action='store_true')

	parser.add_argument('--output_dir', default='',
		        help='path where to save, empty for no saving')
	parser.add_argument('--device', default='cuda',
		        help='device to use for training / testing')
	parser.add_argument('--seed', default=42, type=int)
	parser.add_argument('--resume', default='', help='resume from checkpoint')
	parser.add_argument('--start_epoch', default=0, type=int, metavar='N',
		        help='start epoch')
	parser.add_argument('--eval', action='store_true')
	parser.add_argument('--mixed_precision', action='store_true') # SG Added mixed precision cmd line arg
	parser.add_argument('--num_workers', default=2, type=int)

	# distributed training parameters
	parser.add_argument('--world_size', default=1, type=int,
		        help='number of distributed processes')
	parser.add_argument('--dist_url', default='env://', help='url used to set up distributed training')
	return parser



# for output bounding box post-processing
def box_cxcywh_to_xyxy(x):
    x_c, y_c, w, h = x.unbind(1)
    b = [(x_c - 0.5 * w), (y_c - 0.5 * h),
         (x_c + 0.5 * w), (y_c + 0.5 * h)]
    return torch.stack(b, dim=1)

def rescale_bboxes(out_bbox, size):
    img_w, img_h = size
    b = box_cxcywh_to_xyxy(out_bbox)
    b = b * torch.tensor([img_w, img_h, img_w, img_h], dtype=torch.float32)
    return b

def plot_results(pil_img, prob, boxes):
    plt.figure(figsize=(16,10))
    plt.imshow(pil_img)
    ax = plt.gca()
    colors = COLORS * 100
    for p, (xmin, ymin, xmax, ymax), c in zip(prob, boxes.tolist(), colors):
        ax.add_patch(plt.Rectangle((xmin, ymin), xmax - xmin, ymax - ymin,
                                   fill=False, color=c, linewidth=3))
        cl = p.argmax()
        text = f'{CLASSES[cl]}: {p[cl]:0.2f}'
        ax.text(xmin, ymin, text, fontsize=15,
                bbox=dict(facecolor='yellow', alpha=0.5))
    plt.axis('off')
    plt.show()

if __name__ == '__main__':
	parser = argparse.ArgumentParser('DETR training and evaluation script', parents=[get_args_parser()])
	args = parser.parse_args()

	print(args)


	# send to GPU
	torch.cuda.set_device(0)
	device = torch.device(args.device)


	# using pretrained model for tests
	#model = torch.hub.load("facebookresearch/detr","detr_resnet50", pretrained=True)

	
	print("building model")    
	model,_,_ = build_model(args)


	print("loading checkpoint")
	checkpoint = torch.load("outputs/checkpoint.pth", map_location=args.device)

	print("loading model state")
	model.load_state_dict(checkpoint["model"])
	


	#print("check modules")
	#print(model.backbone[0].body.conv1)

	model.eval()
	#model.to(device)
	#model = model.cuda()

	cap = cv2.VideoCapture(0)
	cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
	cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
	cap.set(cv2.CAP_PROP_FPS, 24)


	transform = T.Compose([
		    T.Resize(800),
		    T.ToTensor(),
		    T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
		])

	for i in range(0,2):
		# Capture frame-by-frame
		ret, frame = cap.read()




		# detr detection and other logic goes here
		img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
		#img.show()


		# propagate through the model
		sample = transform(img).unsqueeze(0)
		#sample = sample.cuda() 
		#sample.to(device)

		with torch.no_grad() :
			outputs = model(sample)

		# keep only predictions with 0.7+ confidence
		probas = outputs['pred_logits'].softmax(-1)[0, :, :-1]
		keep = probas.max(-1).values > 0.7

		# convert boxes from [0; 1] to image scales
		bboxes_scaled = rescale_bboxes(outputs['pred_boxes'][0, keep], img.size)

		# Display the resulting frame
		plot_results(img, probas[keep], bboxes_scaled)


	# When everything done, release the capture
	cap.release()
	cv2.destroyAllWindows()







