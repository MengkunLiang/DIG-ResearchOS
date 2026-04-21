"""机制相似度搜索功能测试。

测试覆盖：
1. 机制关键词提取 - 识别常见技术术语
2. 机制关键词提取 - 空输入处理
3. 机制关键词提取 - 多种技术混合
4. 机制搜索逻辑 - 空关键词处理
5. 机制搜索逻辑 - 正常关键词处理
"""

from __future__ import annotations

import pytest

from researchos.agents.novelty import NoveltyAgent


@pytest.fixture
def novelty_agent():
    """创建 Novelty Agent 实例。"""
    return NoveltyAgent()


def test_extract_mechanism_keywords_common_architectures(novelty_agent):
    """测试提取常见架构关键词。"""
    hypothesis = {
        "title": "Improving Vision Transformers with Attention Mechanisms",
        "content": (
            "We propose a new method that combines Vision Transformer (ViT) "
            "with self-attention and cross-attention mechanisms. "
            "Our approach uses BERT-style pretraining and fine-tuning."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "transformer" in keywords or "vision transformer" in keywords
    assert "attention" in keywords or "self-attention" in keywords
    assert "bert" in keywords
    assert "fine-tuning" in keywords

    # 至少应该提取到几个关键词
    assert len(keywords) >= 3


def test_extract_mechanism_keywords_deep_learning(novelty_agent):
    """测试提取深度学习相关关键词。"""
    hypothesis = {
        "title": "CNN-based Image Classification with ResNet",
        "content": (
            "We use Convolutional Neural Networks (CNN) with ResNet architecture. "
            "The model is trained using Adam optimizer and batch normalization. "
            "We apply transfer learning from pretrained models."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "cnn" in keywords or "convolutional neural network" in keywords
    assert "resnet" in keywords
    assert "adam" in keywords
    assert "batch normalization" in keywords
    assert "transfer learning" in keywords

    assert len(keywords) >= 4


def test_extract_mechanism_keywords_reinforcement_learning(novelty_agent):
    """测试提取强化学习相关关键词。"""
    hypothesis = {
        "title": "PPO-based Reinforcement Learning for Robotics",
        "content": (
            "We apply Proximal Policy Optimization (PPO) algorithm "
            "with actor-critic architecture for robot control. "
            "The agent learns through Q-learning and policy gradient methods."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "ppo" in keywords or "reinforcement learning" in keywords
    assert "actor-critic" in keywords
    assert "q-learning" in keywords or "policy gradient" in keywords

    assert len(keywords) >= 3


def test_extract_mechanism_keywords_generative_models(novelty_agent):
    """测试提取生成模型相关关键词。"""
    hypothesis = {
        "title": "Text Generation with GPT and Diffusion Models",
        "content": (
            "We combine GPT-style language models with Stable Diffusion "
            "for multimodal generation. The system uses VAE for latent encoding "
            "and GAN for adversarial training."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "gpt" in keywords
    assert "diffusion" in keywords or "stable diffusion" in keywords
    assert "vae" in keywords or "variational autoencoder" in keywords
    assert "gan" in keywords or "generative adversarial network" in keywords

    assert len(keywords) >= 3


def test_extract_mechanism_keywords_empty_input(novelty_agent):
    """测试空输入处理。"""
    # 空字典
    keywords = novelty_agent._extract_mechanism_keywords({})
    assert keywords == []

    # 空字符串
    keywords = novelty_agent._extract_mechanism_keywords("")
    assert keywords == []

    # 只有标题没有内容
    keywords = novelty_agent._extract_mechanism_keywords({"title": "Test"})
    assert keywords == []


def test_extract_mechanism_keywords_no_technical_terms(novelty_agent):
    """测试不包含技术术语的假设。"""
    hypothesis = {
        "title": "A Study on User Behavior",
        "content": (
            "We conduct a survey to understand how users interact with systems. "
            "The study involves interviews and questionnaires."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 不应该提取到技术术语
    assert len(keywords) == 0


def test_extract_mechanism_keywords_mixed_case(novelty_agent):
    """测试大小写混合的技术术语。"""
    hypothesis = {
        "title": "Using BERT and GPT for NLP",
        "content": (
            "We use BERT (Bidirectional Encoder Representations from Transformers) "
            "and GPT (Generative Pre-trained Transformer) models. "
            "The Transformer architecture is key to our approach."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语（不区分大小写）
    assert "bert" in keywords
    assert "gpt" in keywords
    assert "transformer" in keywords

    assert len(keywords) >= 3


def test_extract_mechanism_keywords_deduplication(novelty_agent):
    """测试关键词去重。"""
    hypothesis = {
        "title": "Transformer Transformer Transformer",
        "content": (
            "We use transformer architecture. The transformer model is based on "
            "the original transformer paper. Transformers are powerful."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 即使多次出现，也应该只返回一次
    assert keywords.count("transformer") == 1


def test_extract_mechanism_keywords_advanced_techniques(novelty_agent):
    """测试提取高级训练技术关键词。"""
    hypothesis = {
        "title": "Efficient Fine-tuning with LoRA and Adapters",
        "content": (
            "We apply Low-Rank Adaptation (LoRA) and adapter modules "
            "for parameter-efficient fine-tuning. The model uses "
            "contrastive learning and knowledge distillation."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "lora" in keywords
    assert "adapter" in keywords
    assert "fine-tuning" in keywords
    assert "contrastive learning" in keywords
    assert "knowledge distillation" in keywords

    assert len(keywords) >= 4


def test_extract_mechanism_keywords_graph_neural_networks(novelty_agent):
    """测试提取图神经网络相关关键词。"""
    hypothesis = {
        "title": "Graph Neural Networks for Social Network Analysis",
        "content": (
            "We use Graph Convolutional Networks (GCN) and other "
            "Graph Neural Network (GNN) architectures for node classification."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "gnn" in keywords or "graph neural network" in keywords
    assert "gcn" in keywords

    assert len(keywords) >= 2


def test_search_similar_mechanisms_empty_keywords(novelty_agent):
    """测试空关键词列表的机制搜索。"""
    # 空关键词列表应该返回空结果
    papers = novelty_agent._search_similar_mechanisms([], None)
    assert papers == []


def test_search_similar_mechanisms_with_keywords(novelty_agent):
    """测试有关键词的机制搜索。"""
    keywords = ["transformer", "attention", "bert"]

    # 注意：当前实现返回空列表，因为实际搜索在 agent 运行时完成
    papers = novelty_agent._search_similar_mechanisms(keywords, None)

    # 当前实现应该返回空列表
    assert papers == []


def test_search_similar_mechanisms_many_keywords(novelty_agent):
    """测试大量关键词的机制搜索（应该只使用前几个）。"""
    keywords = [
        "transformer",
        "bert",
        "gpt",
        "attention",
        "cnn",
        "resnet",
        "lstm",
        "gru",
        "gan",
        "vae",
    ]

    # 应该能处理大量关键词（内部会限制数量）
    papers = novelty_agent._search_similar_mechanisms(keywords, None)

    # 当前实现应该返回空列表
    assert papers == []


def test_extract_mechanism_keywords_retrieval_augmented(novelty_agent):
    """测试提取检索增强生成相关关键词。"""
    hypothesis = {
        "title": "RAG for Question Answering",
        "content": (
            "We use Retrieval-Augmented Generation (RAG) to improve "
            "question answering performance. The system combines "
            "retrieval with generation."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "rag" in keywords or "retrieval-augmented generation" in keywords
    assert "retrieval" in keywords

    assert len(keywords) >= 1


def test_extract_mechanism_keywords_few_shot_learning(novelty_agent):
    """测试提取少样本学习相关关键词。"""
    hypothesis = {
        "title": "Few-shot Learning with Meta-Learning",
        "content": (
            "We apply few-shot learning and zero-shot learning techniques "
            "using meta-learning approaches for rapid adaptation."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "few-shot learning" in keywords
    assert "zero-shot learning" in keywords
    assert "meta-learning" in keywords

    assert len(keywords) >= 3


def test_extract_mechanism_keywords_optimization_algorithms(novelty_agent):
    """测试提取优化算法关键词。"""
    hypothesis = {
        "title": "Training with AdamW and SGD",
        "content": (
            "We compare AdamW optimizer with SGD and RMSprop. "
            "The models are trained using gradient descent with momentum."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "adamw" in keywords
    assert "sgd" in keywords
    assert "rmsprop" in keywords
    assert "gradient descent" in keywords or "momentum" in keywords

    assert len(keywords) >= 3


def test_extract_mechanism_keywords_model_compression(novelty_agent):
    """测试提取模型压缩相关关键词。"""
    hypothesis = {
        "title": "Model Compression via Pruning and Quantization",
        "content": (
            "We apply pruning and quantization techniques for model compression. "
            "Neural Architecture Search (NAS) is used to find efficient architectures."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "pruning" in keywords
    assert "quantization" in keywords
    assert "compression" in keywords
    assert "nas" in keywords or "neural architecture search" in keywords

    assert len(keywords) >= 3


def test_extract_mechanism_keywords_string_input(novelty_agent):
    """测试字符串输入（非字典）。"""
    hypothesis_text = (
        "We use Transformer architecture with BERT pretraining "
        "and fine-tuning for downstream tasks."
    )

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis_text)

    # 应该能处理字符串输入
    assert "transformer" in keywords
    assert "bert" in keywords
    assert "fine-tuning" in keywords

    assert len(keywords) >= 3


def test_extract_mechanism_keywords_recurrent_networks(novelty_agent):
    """测试提取循环神经网络相关关键词。"""
    hypothesis = {
        "title": "Sequence Modeling with LSTM and GRU",
        "content": (
            "We use Long Short-Term Memory (LSTM) and Gated Recurrent Unit (GRU) "
            "networks for sequence modeling. These Recurrent Neural Networks (RNN) "
            "are effective for temporal data."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 应该识别出这些技术术语
    assert "lstm" in keywords
    assert "gru" in keywords
    assert "rnn" in keywords or "recurrent neural network" in keywords

    assert len(keywords) >= 3
