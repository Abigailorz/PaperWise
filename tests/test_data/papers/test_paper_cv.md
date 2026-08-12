# SegNet-Lite: Efficient Semantic Segmentation with Depthwise Separable Convolutions

## Abstract
Real-time semantic segmentation is critical for autonomous driving and robotics,
but high-accuracy models are often too slow for edge devices. We propose SegNet-Lite,
a lightweight encoder-decoder architecture built on depthwise separable convolutions
and a novel Light Decoder module. On Cityscapes validation, SegNet-Lite achieves
78.3% mIoU at 42.1 FPS on a single NVIDIA Jetson Orin, with only 2.3M parameters —
significantly more efficient than existing real-time models.

## 1 Introduction
Semantic segmentation assigns a class label to every pixel. State-of-the-art models
such as PSPNet and DeepLabV3 achieve high accuracy but require hundreds of
millions of parameters and are unsuitable for embedded deployment. This paper
targets the accuracy-efficiency tradeoff for real-time applications.

## 2 Related Work
BiSeNet uses a two-pathway design with spatial and context branches.
Fast-SCNN shares a single backbone across tasks to reduce computation.
ICNet employs cascade image resolution to speed up inference.
None of them fully exploits depthwise separable convolutions for
encoder-decoder segmentation.

## 3 Method
### 3.1 Backbone
SegNet-Lite uses a modified MobileNetV2 backbone with inverted residual blocks.
The first three stages run at full resolution; the later stages use stride-2
downsampling to build a compact feature hierarchy.

### 3.2 Light Decoder
Instead of expensive multi-scale fusion, our Light Decoder concatenates the
encoder features with a single upsampled context tensor, then applies a
1x1 convolution to produce the final segmentation map. This avoids the
computational cost of feature pyramids while retaining spatial detail.

### 3.3 Auxiliary Loss
An auxiliary loss is applied at the third stage to improve gradient flow.
The total loss is L = L_main + 0.4 * L_aux.

## 4 Experiments
### 4.1 Setup
We evaluate on Cityscapes validation set (500 images, 19 classes).
Models are trained for 300 epochs with Adam, initial learning rate 1e-3,
poly decay, and batch size 16.

### 4.2 Results
Table 1 reports the main comparison on Cityscapes validation:

| Model | mIoU (%) | FPS | Params (M) |
|-------|----------|-----|------------|
| PSPNet | 81.2 | 5.2 | 68.1 |
| DeepLabV3 | 82.5 | 4.1 | 62.3 |
| BiSeNet | 77.9 | 33.0 | 49.0 |
| ICNet | 69.5 | 40.1 | 26.3 |
| SegNet-Lite (ours) | 78.3 | 42.1 | 2.3 |

### 4.3 Ablation
Removing the depthwise separable convolutions drops mIoU by 1.9 points
but increases parameters to 8.7M. Removing the auxiliary loss reduces
final mIoU by 1.2 points. Using a feature pyramid instead of the Light
Decoder improves mIoU by 0.4 points but reduces FPS from 42.1 to 18.7.

## 5 Limitations
SegNet-Lite struggles with small objects (e.g., traffic signs) and
class imbalance in rare classes. It is sensitive to weather conditions
such as fog and heavy rain, and its accuracy on unseen cities degrades
due to domain shift.

## 6 Conclusion
SegNet-Lite demonstrates that depthwise separable convolutions combined with
a lightweight decoder can achieve competitive accuracy at real-time speed.
Future work includes knowledge distillation from larger teachers and
domain adaptation for adverse weather.
