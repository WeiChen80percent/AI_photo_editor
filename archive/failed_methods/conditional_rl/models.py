import gymnasium as gym
import torch
import torch.nn as nn
from stable_baselines3 import PPO
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.callbacks import CheckpointCallback

# 匯入自定義環境
from darktable_env import DarktableEnv

class MultiModalFeatureExtractor(BaseFeaturesExtractor):
    """
    特徵提取網路：將 F_expert(2) + F_color(512) 融合降維。
    Stable-Baselines3 的自定義特徵提取器。
    """
    def __init__(self, observation_space: gym.spaces.Box, features_dim: int = 128):
        # 這裡設定最終輸出給 Actor-Critic 的特徵維度 (128)
        super(MultiModalFeatureExtractor, self).__init__(observation_space, features_dim)
        
        self.expert_dim = 2 # 假設兩個專家 C 和 E
        self.color_dim = 512
        
        # 1. 處理專家 ID 特徵
        self.expert_net = nn.Sequential(
            nn.Linear(self.expert_dim, 16),
            nn.ReLU()
        )
        
        # 2. 處理色彩直方圖特徵
        self.color_net = nn.Sequential(
            nn.Linear(self.color_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 128),
            nn.ReLU()
        )
        
        # 3. 特徵融合
        self.fusion_net = nn.Sequential(
            nn.Linear(16 + 128, 128),
            nn.ReLU(),
            nn.Linear(128, features_dim),
            nn.ReLU()
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        # observations shape: (batch_size, 514)
        # 切割特徵
        f_expert = observations[:, 0:self.expert_dim]
        f_color = observations[:, self.expert_dim:]
        
        # 專家特徵
        expert_features = self.expert_net(f_expert)
        
        # 處理視覺特徵
        color_features = self.color_net(f_color)
        
        # 融合
        fused = self.fusion_net(torch.cat([expert_features, color_features], dim=1))
        return fused

from stable_baselines3.common.vec_env import SubprocVecEnv
from stable_baselines3.common.monitor import Monitor

def make_env(env_id):
    """建立獨立環境的閉包函數，避免記憶體或暫存檔衝突"""
    def _init():
        env = DarktableEnv(
            data_dir="../data",
            features_path="dataset_features.pkl",
            darktable_cli="C:/Program Files/darktable/bin/darktable-cli.exe",
            env_id=env_id
        )
        return Monitor(env) # 必須包裝 Monitor 才能讓 SB3 追蹤並印出 Reward
    return _init

def train():
    """訓練主程式"""
    print("初始化多核心 Darktable 平行訓練環境...")
    
    # 根據您的需求設定為 12 核心
    num_cpu = 12 
    
    # 建立平行化環境
    env = SubprocVecEnv([make_env(i) for i in range(num_cpu)])
    
    # 準備自定義網路架構
    policy_kwargs = dict(
        features_extractor_class=MultiModalFeatureExtractor,
        features_extractor_kwargs=dict(features_dim=256),
        # 設定 Actor 與 Critic 的隱藏層 (已修正警告)
        net_arch=dict(pi=[128, 128], vf=[128, 128])
    )
    
    # 設定 Tensorboard 與 Checkpoint
    checkpoint_callback = CheckpointCallback(
        save_freq=5000,
        save_path='./models_checkpoints/',
        name_prefix='rl_photo_agent'
    )
    
    print("建立 PPO 模型...")
    # 由於我們的環境每個 episode 只有 1 個 step (One-shot policy)
    model = PPO(
        "MlpPolicy", 
        env, 
        policy_kwargs=policy_kwargs, 
        verbose=1, 
        tensorboard_log="./tensorboard_logs/",
        learning_rate=3e-4,
        n_steps=32,      # 每個環境收集 32 步，12核總共會收集 384 步才更新一次
        batch_size=32,   # Batch Size
        n_epochs=10      # 每個 Batch 更新 10 次
    )
    
    print("開始平行訓練 (這將需要一段時間)...")
    # 把總步數拉長到 5 萬步，因為現在我們有 12 核平行加速
    model.learn(total_timesteps=50000, callback=checkpoint_callback)
    
    print("儲存最終權重...")
    model.save("final_conditional_rl_model")

if __name__ == "__main__":
    # 在 Windows 上進行平行處理必須要有 __main__ 保護
    train()
