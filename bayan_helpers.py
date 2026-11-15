"""Shared Colab helper — also embedded into train_wakeword.ipynb cell 2."""
import os
import yaml

MODEL_NAME = "bayan"
TARGET_PHRASE = ["bayan", "hey bayan", "hi bayan"]


def ensure_bayan_config(path="/content/my_model.yaml", force=False):
    """Create or load my_model.yaml for the bayan wake word."""
    cfg = {
        "target_phrase": list(TARGET_PHRASE),
        "model_name": MODEL_NAME,
        "custom_negative_phrases": [],
        "n_samples": 2000,
        "n_samples_val": 1000,
        "tts_batch_size": 50,
        "piper_sample_generator_path": "/content/piper-sample-generator",
        "augmentation_rounds": 1,
        "augmentation_batch_size": 16,
        "steps": 20000,
        "max_negative_weight": 1500,
        "target_accuracy": 0.7,
        "target_recall": 0.5,
        "target_false_positives_per_hour": 0.5,
        "batch_size": 128,
        "learning_rate": 1e-4,
        "model_type": "dnn",
        "layer_dim": 128,
        "layer_size": 128,
        "n_blocks": 1,
        "model_input_shape": [16, 96],
        "n_classes": 1,
        "batch_n_per_class": {
            "ACAV100M_sample": 1024,
            "adversarial_negative": 50,
            "positive": 50,
        },
        "background_paths": ["/content/fma_wav"],
        "background_paths_duplication_rate": [1],
        "rir_paths": ["/content/mit_rirs"],
        "false_positive_validation_data_path": "/content/acav_val_subset.npy",
        "feature_data_files": {"ACAV100M_sample": "/content/acav_train_subset.npy"},
        "output_dir": f"/content/{MODEL_NAME}_output",
        "tflite_export": False,
        "onnx_export": True,
        "positive_clips_train_dir": f"/content/{MODEL_NAME}_output/{MODEL_NAME}/positive_train",
        "positive_clips_test_dir": f"/content/{MODEL_NAME}_output/{MODEL_NAME}/positive_test",
        "negative_clips_train_dir": f"/content/{MODEL_NAME}_output/{MODEL_NAME}/negative_train",
        "negative_clips_test_dir": f"/content/{MODEL_NAME}_output/{MODEL_NAME}/negative_test",
        "feature_save_dir": f"/content/{MODEL_NAME}_output/{MODEL_NAME}",
    }
    if force or not os.path.exists(path):
        os.makedirs(cfg["output_dir"], exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, sort_keys=False)
        print(f"  wrote {path}")
        return cfg
    with open(path, encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    return loaded or cfg
