import json
import os
import ast
import warnings
from datetime import datetime
from typing import Literal
from diffusers import StableDiffusionPipeline, DDPMScheduler, StableDiffusionXLPipeline
import torch
import subprocess
import sys
import torch.nn as nn
from pprint import pprint
from accelerate import Accelerator
import gradio as gr
from modules.scripts import basedir
from modules import shared

warnings.filterwarnings("ignore", category=FutureWarning)

from diffusers import (SchedulerMixin, StableDiffusionPipeline,
                       StableDiffusionXLPipeline)
from diffusers import (
    StableDiffusionPipeline,
    DDPMScheduler,
    EulerAncestralDiscreteScheduler,
    DPMSolverMultistepScheduler,
    DPMSolverSinglestepScheduler,
    LMSDiscreteScheduler,
    PNDMScheduler,
    DDIMScheduler,
    EulerDiscreteScheduler,
    HeunDiscreteScheduler,
    KDPM2DiscreteScheduler,
    KDPM2AncestralDiscreteScheduler,
    AutoencoderKL,
)

all_configs = []

PASS2 = "2nd pass"
POs = ["came", "tiger", "adammini"]

path_root = basedir()
jsonspath = os.path.join(path_root,"jsons")
logspath = os.path.join(path_root,"logs")
presetspath = os.path.join(path_root,"presets")

class Trainer():
    def __init__(self, jsononly, model, vae, mode, values):
        self.values = values
        self.mode = mode
        self.use_8bit = False
        self.count_dict = {}
        self.metadata = {}

        self.save_dir = shared.cmd_opts.lora_dir
        self.setpass(0)

        self.image_size = [int(x) for x in self.image_size.split(",")]
        if len(self.image_size) == 1:
            self.image_size = self.image_size * 2
        self.image_size.sort()

        self.train_min_timesteps = 0
        self.train_max_timesteps = 1000
        self.gradient_accumulation_steps = 1
        self.train_repeat = 1
        self.total_images = 0
        
        if self.diff_1st_pass_only:
            self.save_1st_pass = True

        self.checkfile()

        clen = len(all_configs) * (len(values) // len(all_configs))

        self.prompts = values[clen:clen + 3]

        self.images =  values[clen + 3:]
        
        self.add_dcit = {"mode": mode, "model": model, "vae": vae, "original prompt": self.prompts[0],"target prompt": self.prompts[1]}

        self.export_json(jsononly)

    def setpass(self, pas, set = True):
        values_0 = self.values[:len(all_configs)]
        values_1 = self.values[len(all_configs):len(all_configs) * 2]
        if pas == 1:
            if values_1[-1]:
                if set: print("Use 2nd pass settings")
            else:
                return
        jdict = {}
        for i, (sets, value) in enumerate(zip(all_configs, values_1 if pas > 0 else values_0)):
            jdict[sets[0]] = value
    
            if pas > 0:
                if not sets[5][3]:
                    value = values_0[i]

            if not isinstance(value, sets[4]):
                try:
                    value = sets[4](value)
                except:
                    value = sets[3]
            if "precision" in sets[0]:
                if sets[0] == "train_model_precision" and value == "fp8":
                    self.use_8bit == True
                    print("Use 8bit Model Precision")
                value = parse_precision(value)

            if "diff_load_1st_pass" == sets[0]:
                found = False
                value = value if ".safetensors" in value else value + ".safetensors"
                for root, dirs, files in os.walk(self.save_dir):
                    if value in files:
                        value = os.path.join(root, value)
                        found = True
                if not found:
                    value = ""
            
            if "train_optimizer" == sets[0]:
                value = value.lower()
            
            if "train_optimizer_settings" == sets[0] or "train_lr_scheduler_settings" == sets[0]:
                dvalue = {}
                if value is not None and len(value.strip()) > 0:
                    # 改行や空白を取り除きつつ処理
                    value = value.replace(" ", "").replace(";","\n")
                    args = value.split("\n")
                    for arg in args:
                        if "=" in arg:  # "=" が存在しない場合は無視
                            key, val = arg.split("=", 1)
                            val = ast.literal_eval(val)  # リテラル評価で型を適切に変換
                            dvalue[key] = val
                value = dvalue
            if set: 
                setattr(self, sets[0].split("(")[0], value)

        return jdict

    savedata = ["model", "vae", ]
    
    def export_json(self, jsononly):
        current_time = datetime.now()
        outdict = self.setpass(0, set = False)
        if self.mode == "Difference":
            outdict[PASS2] = self.setpass(1, set = False)
        outdict.update(self.add_dcit)
        today = current_time.strftime("%Y%m%d")
        time = current_time.strftime("%Y%m%d_%H%M%S")
        add = "" if jsononly else f"-{time}"
        jsonpath = os.path.join(presetspath, self.save_lora_name + add + ".json")  if jsononly else os.path.join(jsonspath, today, self.save_lora_name + add + ".json")
        self.csvpath = os.path.join(logspath ,today, self.save_lora_name + add + ".csv")
        
        if self.save_as_json:
            directory = os.path.dirname(jsonpath)
            if not os.path.exists(directory):
                os.makedirs(directory)
            with open(jsonpath, 'w') as file:
                json.dump(outdict, file, indent=4)
        
        if jsononly:
            with open(presetspath, 'w') as file:
                json.dump(outdict, file, indent=4)  

    def db(self, *obj, pp = False):
        if self.logging_verbose:
            if pp:
                pprint(*obj)
            else:
                print(*obj)

    def checkfile(self):
        if self.save_lora_name == "":
            self.save_lora_name = "untitled"

        filename = os.path.join(self.save_dir, f"{self.save_lora_name}.safetensors")

        self.isfile = os.path.isfile(filename) and not self.save_overwrite
    
    def tagcount(self, prompt):
        tags = [p.strip() for p in prompt.split(",")]

        for tag in tags:
            if tag in self.count_dict:
                self.count_dict[tag] += 1
            else:
                self.count_dict[tag] = 1


def import_json(name, preset = False):
    def find_files(file_name):
        for root, dirs, files in os.walk(jsonspath):
            if file_name in files:
                return os.path.join(root, file_name)
        return None
    if preset:
        filepath = os.path.join(presetspath, name + ".json")
    else:
        filepath = find_files(name if ".json" in name else name + ".json")

    output = []

    if filepath is None:
        return [gr.update()] * len(all_configs)
    with open(filepath, 'r', encoding='utf-8') as file:
        data = json.load(file)
    
    def setconfigs(data, output):
        for key, gtype ,_ ,default , dtype, _ in all_configs:
            from scripts.traintrain import OPTIMIZERS
            if key in data:
                if key == "train_optimizer":
                    for optim in OPTIMIZERS:
                        if data[key].lower() == optim.lower():
                            data[key] = optim 
                if key == PASS2: continue
                if gtype == "DD" or "learning rate" in key:
                    dtype = str
                try:
                    output.append(dtype(data[key]))
                except:
                    output.append(default)
            else:
                output.append(default)
    setconfigs(data, output)

    if PASS2 in data and data[PASS2]:
        setconfigs(data[PASS2], output)
    else:
        output = output * 2

    output.append(data["original prompt"] if "original prompt" in data else "")
    output.append(data["target prompt"] if "target prompt" in data else "")
    output.append("")
    
    head = []
    head.append(data["mode"] if "mode" in data else "LoRA")
    head.append(data["model"] if "model" in data else None)
    head.append(data["vae"] if "vae" in data else None)

    return head + output

AVAILABLE_SCHEDULERS = Literal["ddim", "ddpm", "lms", "euler_a"]

def get_optimizer(name: str, trainable_params, lr, optimizer_kwargs, network):
    name = name.lower()
    if name.startswith("dadapt"):
        import dadaptation
        if name == "dadaptadam":
            optim = dadaptation.DAdaptAdam
        elif name == "dadaptlion":
            optim = dadaptation.DAdaptLion               
        elif name == "DAdaptAdaGrad".lower():
            optim = dadaptation.DAdaptAdaGrad
        elif name == "DAdaptAdan".lower():
            optim = dadaptation.DAdaptAdan
        elif name == "DAdaptSGD".lower():
            optim = dadaptation.DAdaptSGD

    elif name.endswith("8bit"): 
        import bitsandbytes as bnb
        try:
            if name == "adam8bit":
                optim = bnb.optim.Adam8bit
            elif name == "adamw8bit":  
                optim = bnb.optim.AdamW8bit
            elif name == "SGDNesterov8bit".lower():
                optim = bnb.optim.SGD8bit
                if "momentum" not in optimizer_kwargs:
                    optimizer_kwargs["momentum"] = 0.9
                optimizer_kwargs["nesterov"] = True
            elif name == "Lion8bit".lower():
                optim = bnb.optim.Lion8bit
            elif name == "PagedAdamW8bit".lower():
                optim = bnb.optim.PagedAdamW8bit
            elif name == "PagedLion8bit".lower():
                optim  = bnb.optim.PagedLion8bit

        except AttributeError:
            raise AttributeError(
                f"No {name}. The version of bitsandbytes installed seems to be old. Please install newest. / {name}が見つかりません。インストールされているbitsandbytesのバージョンが古いようです。最新版をインストールしてください。"
            )

    elif name.lower() == "adafactor":
        import transformers
        optim = transformers.optimization.Adafactor

    elif name == "PagedAdamW".lower():
        import bitsandbytes as bnb
        optim = bnb.optim.PagedAdamW
    elif name == "PagedAdamW32bit".lower():
        import bitsandbytes as bnb
        optim = bnb.optim.PagedAdamW32bit

    elif name == "SGDNesterov".lower():
        if "momentum" not in optimizer_kwargs:
            optimizer_kwargs["momentum"] = 0.9
        optimizer_kwargs["nesterov"] = True
        optim = torch.optim.SGD

    elif name.endswith("schedulefree".lower()):
        import schedulefree as sf
        if name == "RAdamScheduleFree".lower():
            optim = sf.RAdamScheduleFree
        elif name == "AdamWScheduleFree".lower():
            optim = sf.AdamWScheduleFree
        elif name == "SGDScheduleFree".lower():
            optim = sf.SGDScheduleFree

    elif name in POs:
        import pytorch_optimizer as po    
        if name == "CAME".lower():
            optim = po.CAME        
        elif name == "Tiger".lower():
            optim = po.Tiger        
        elif name == "AdamMini".lower():
            optim = po.AdamMini   
        
    else:
        if name == "adam":
            optim = torch.optim.Adam
        elif name == "adamw":
            optim = torch.optim.AdamW  
        elif name == "lion":
            from lion_pytorch import Lion
            optim = Lion
        elif name == "prodigy":
            import prodigyopt
            optim = prodigyopt.Prodigy

    
    if name.startswith("DAdapt".lower()) or name == "Prodigy".lower():
    # check lr and lr_count, and logger.info warning
        actual_lr = lr
        lr_count = 1
        if type(trainable_params) == list and type(trainable_params[0]) == dict:
            lrs = set()
            actual_lr = trainable_params[0].get("lr", actual_lr)
            for group in trainable_params:
                lrs.add(group.get("lr", actual_lr))
            lr_count = len(lrs)

        if actual_lr <= 0.1:
            print(
                f"learning rate is too low. If using D-Adaptation or Prodigy, set learning rate around 1.0 / 学習率が低すぎるようです。D-AdaptationまたはProdigyの使用時は1.0前後の値を指定してください: lr={actual_lr}"
            )
            print("recommend option: lr=1.0 / 推奨は1.0です")
        if lr_count > 1:
            print(
                f"when multiple learning rates are specified with dadaptation (e.g. for Text Encoder and U-Net), only the first one will take effect / D-AdaptationまたはProdigyで複数の学習率を指定した場合（Text EncoderとU-Netなど）、最初の学習率のみが有効になります: lr={actual_lr}"
            )

    elif name == "Adafactor".lower():
        # 引数を確認して適宜補正する
        if "relative_step" not in optimizer_kwargs:
            optimizer_kwargs["relative_step"] = True  # default
        if not optimizer_kwargs["relative_step"] and optimizer_kwargs.get("warmup_init", False):
            print(
                f"set relative_step to True because warmup_init is True / warmup_initがTrueのためrelative_stepをTrueにします"
            )
            optimizer_kwargs["relative_step"] = True
 
        if optimizer_kwargs["relative_step"]:
            print(f"relative_step is true / relative_stepがtrueです")
            if lr != 0.0:
                print(f"learning rate is used as initial_lr / 指定したlearning rateはinitial_lrとして使用されます")
  

            # trainable_paramsがgroupだった時の処理：lrを削除する
            if type(trainable_params) == list and type(trainable_params[0]) == dict:
                has_group_lr = False
                for group in trainable_params:
                    p = group.pop("lr", None)
                    has_group_lr = has_group_lr or (p is not None)

            lr = None
        #TODO

        # else:
        #     if args.max_grad_norm != 0.0:
        #         logger.warning(
        #             f"because max_grad_norm is set, clip_grad_norm is enabled. consider set to 0 / max_grad_normが設定されているためclip_grad_normが有効になります。0に設定して無効にしたほうがいいかもしれません"
        #         )
        #     if args.lr_scheduler != "constant_with_warmup":
        #         logger.warning(f"constant_with_warmup will be good / スケジューラはconstant_with_warmupが良いかもしれません")
        #     if optimizer_kwargs.get("clip_threshold", 1.0) != 1.0:
        #         logger.warning(f"clip_threshold=1.0 will be good / clip_thresholdは1.0が良いかもしれません")


    return optim(network, lr = lr, **optimizer_kwargs) if name == "AdamMini".lower() else  optim(trainable_params, lr = lr, **optimizer_kwargs) 


def get_random_resolution_in_bucket(bucket_resolution: int = 512) -> tuple[int, int]:
    max_resolution = bucket_resolution
    min_resolution = bucket_resolution // 2

    step = 64

    min_step = min_resolution // step
    max_step = max_resolution // step

    height = torch.randint(min_step, max_step, (1,)).item() * step
    width = torch.randint(min_step, max_step, (1,)).item() * step

    return height, width

def load_noise_scheduler(name: str,v_parameterization: bool):
    sched_init_args = {}
    name = name.lower().replace(" ", "_")
    if name == "ddim":
        scheduler_cls = DDIMScheduler
    elif name == "ddpm":
        scheduler_cls = DDPMScheduler
    elif name == "pndm":
        scheduler_cls = PNDMScheduler
    elif name == "lms" or name == "k_lms":
        scheduler_cls = LMSDiscreteScheduler
    elif name == "euler" or name == "k_euler":
        scheduler_cls = EulerDiscreteScheduler
    elif name == "euler_a" or name == "k_euler_a":
        scheduler_cls = EulerAncestralDiscreteScheduler
    elif name == "dpmsolver" or name == "dpmsolver++":
        scheduler_cls = DPMSolverMultistepScheduler
        sched_init_args["algorithm_type"] = name
    elif name == "dpmsingle":
        scheduler_cls = DPMSolverSinglestepScheduler
    elif name == "heun":
        scheduler_cls = HeunDiscreteScheduler
    elif name == "dpm_2" or name == "k_dpm_2":
        scheduler_cls = KDPM2DiscreteScheduler
    elif name == "dpm_2_a" or name == "k_dpm_2_a":
        scheduler_cls = KDPM2AncestralDiscreteScheduler
    else:
        scheduler_cls = DDIMScheduler
        "Selected scheduler is not in list, use DDIMScheduler."

    if v_parameterization:
        sched_init_args["prediction_type"] = "v_prediction"

    scheduler = scheduler_cls(
        num_train_timesteps = 1000,
        beta_start = 0.00085,
        beta_end = 0.0120, 
        beta_schedule = "scaled_linear",
        **sched_init_args,
    )

    # clip_sample=Trueにする
    if hasattr(scheduler.config, "clip_sample") and scheduler.config.clip_sample is False:
        # print("set clip_sample to True")
        scheduler.config.clip_sample = True

    prepare_scheduler_for_custom_training(scheduler)

    return scheduler

def prepare_scheduler_for_custom_training(noise_scheduler):
    if hasattr(noise_scheduler, "all_snr"):
        return

    alphas_cumprod = noise_scheduler.alphas_cumprod
    sqrt_alphas_cumprod = torch.sqrt(alphas_cumprod)
    sqrt_one_minus_alphas_cumprod = torch.sqrt(1.0 - alphas_cumprod)
    alpha = sqrt_alphas_cumprod
    sigma = sqrt_one_minus_alphas_cumprod
    all_snr = (alpha / sigma) ** 2

    noise_scheduler.all_snr = all_snr.to("cuda")


def load_checkpoint_model(checkpoint_path, t, clip_skip = None, vae = None):
    pipe = StableDiffusionPipeline.from_single_file(checkpoint_path,upcast_attention=True if t.isv2 else False,vae = vae)

    tokenizer = pipe.tokenizer
    text_encoder = pipe.text_encoder
    unet = pipe.unet
    vae = pipe.vae

    if clip_skip is not None:
        if t.isv2:
            text_encoder.config.num_hidden_layers = 24 - (clip_skip - 1)
        else:
            text_encoder.config.num_hidden_layers = 12 - (clip_skip - 1)

    text_model = TextModel(tokenizer, None, text_encoder, None)
    
    del pipe

    return text_model, unet, vae

def load_checkpoint_model_xl(checkpoint_path, t, vae = None):

    pipe = StableDiffusionXLPipeline.from_single_file(checkpoint_path)

    tokenizer = pipe.tokenizer
    tokenizer_2 = pipe.tokenizer_2
    text_encoder = pipe.text_encoder
    text_encoder_2 = pipe.text_encoder_2
    unet = pipe.unet
    vae = pipe.vae

    text_model = TextModel(tokenizer, tokenizer_2, text_encoder, text_encoder_2)
    
    del pipe

    return text_model, unet, vae

class TextModel(nn.Module):
    def __init__(self, tokenizer, tokenizer_2, text_encoder, text_encoder_2, clip_skip=-1):
        super().__init__()
        self.tokenizer = tokenizer
        self.tokenizer_2 = tokenizer_2
        self.tokenizer_list = [tokenizer] if tokenizer_2 is None else [tokenizer, tokenizer_2]

        self.text_encoder = text_encoder
        self.text_encoder_2 = text_encoder_2
        self.text_encoder_list = [text_encoder] if text_encoder_2 is None else [text_encoder, text_encoder_2]

        self.clip_skip = clip_skip
        self.sdxl = tokenizer_2 is not None

        self.textual_inversion = False

    def tokenize(self, texts):
        tokens = self.tokenizer(texts, max_length=self.tokenizer.model_max_length, padding="max_length",
                                truncation=True, return_tensors='pt').input_ids.to(self.text_encoder.device)
        if self.sdxl:
            tokens_2 = self.tokenizer_2(texts, max_length=self.tokenizer_2.model_max_length, padding="max_length",
                                        truncation=True, return_tensors='pt').input_ids.to(self.text_encoder_2.device)
            empty_text = []
            for text in texts:
                if text == "":
                    empty_text.append(True)
                else:
                    empty_text.append(False)
        else:
            tokens_2 = None
            empty_text = None

        return tokens, tokens_2, empty_text

    def forward(self, tokens, tokens_2=None, empty_text=None):
        encoder_hidden_states = self.text_encoder(tokens, output_hidden_states=True).hidden_states[self.clip_skip]
        if self.sdxl:
            encoder_output_2 = self.text_encoder_2(tokens_2, output_hidden_states=True)
            
            # calculate pooled_output
            last_hidden_state = encoder_output_2.last_hidden_state
            eos_token_index = torch.where(tokens_2 == self.tokenizer_2.eos_token_id)[1].to(device=last_hidden_state.device)
            pooled_output = last_hidden_state[
                torch.arange(last_hidden_state.shape[0], device=last_hidden_state.device),
                eos_token_index
            ]
            pooled_output = self.text_encoder_2.text_projection(pooled_output)

            encoder_hidden_states_2 = encoder_output_2.hidden_states[self.clip_skip]
            encoder_hidden_states = torch.cat([encoder_hidden_states, encoder_hidden_states_2], dim=2)

            # pooled_output is zero vector for empty text
            if empty_text is not None:
                for i, empty in enumerate(empty_text):
                    if empty:
                        pooled_output[i] = torch.zeros_like(pooled_output[i])
        else:
            encoder_hidden_states = self.text_encoder.text_model.final_layer_norm(encoder_hidden_states)
            pooled_output = None

        return encoder_hidden_states, pooled_output

    def encode_text(self, texts):
        tokens, tokens_2, empty_text = self.tokenize(texts)
        encoder_hidden_states, pooled_output = self.forward(tokens, tokens_2, empty_text)
        return encoder_hidden_states, pooled_output

    def gradient_checkpointing_enable(self, enable=True):
        if enable:
            self.text_encoder.gradient_checkpointing_enable()
            if self.sdxl:
                self.text_encoder_2.gradient_checkpointing_enable()
        else:
            self.text_encoder.gradient_checkpointing_disable()
            if self.sdxl:
                self.text_encoder_2.gradient_checkpointing_disable()

    def to(self, device = None, dtype = None):
        self.text_encoder.to(device,dtype)
        if self.text_encoder_2 is not None:
            self.text_encoder_2.to(device,dtype)

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

def make_accelerator(t):
    accelerator = Accelerator(
        gradient_accumulation_steps=t.gradient_accumulation_steps,
        mixed_precision=parse_precision(t.train_model_precision, mode = False)
    )

    return accelerator

def parse_precision(precision, mode = True):
    if mode:
        if precision == "fp32" or precision == "float32":
            return torch.float32
        elif precision == "fp16" or precision == "float16" or precision == "fp8":
            return torch.float16
        elif precision == "bf16" or precision == "bfloat16":
            return torch.bfloat16
    else:
        if precision == torch.float16 or precision == "fp8":
            return 'fp16'
        elif precision == torch.bfloat16:
            return 'bf16'

    raise ValueError(f"Invalid precision type: {precision}")
