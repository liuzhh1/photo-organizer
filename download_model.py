"""An explicit one-time download; the application itself always infers offline."""
import bootstrap  # noqa: F401
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='下载 Hugging Face SigLIP 2 模型到本地')
    parser.add_argument('--model', default='google/siglip2-base-patch16-224')
    parser.add_argument('--destination', type=Path,
                        default=Path(__file__).resolve().parent / '.models' / 'siglip2-base-patch16-224')
    parser.add_argument('--revision', default='main')
    parser.add_argument('--endpoint', default=None, help='Optional trusted Hugging Face-compatible model endpoint')
    args = parser.parse_args()
    import os
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    if args.endpoint:
        os.environ['HF_ENDPOINT'] = args.endpoint
    from huggingface_hub import snapshot_download
    result = snapshot_download(repo_id=args.model, revision=args.revision,
                               local_dir=str(args.destination),
                               allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt'],
                               max_workers=2)
    from model import LocalModel
    status = LocalModel.status({'model_path': str(result)})
    if not status['ready']:
        raise RuntimeError('模型下载未完成：' + status['problem'])
    print(f'下载完成。在拾光设置中选择此目录：\n{result}')


if __name__ == '__main__':
    main()
