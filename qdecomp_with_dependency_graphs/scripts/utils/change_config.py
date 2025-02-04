import argparse
import os

from tempfile import TemporaryDirectory
import tarfile
from allennlp.common.params import Params
from pathlib import Path

from typing import Dict, Any, Union


def change_config(model_path:str, overrides: Union[str,Dict[str, Any]]):
    with TemporaryDirectory() as tmpdirname:
        with tarfile.open(model_path, mode='r:gz') as input_tar:
            print('Extracting model...')
            def is_within_directory(directory, target):
                
                abs_directory = os.path.abspath(directory)
                abs_target = os.path.abspath(target)
            
                prefix = os.path.commonprefix([abs_directory, abs_target])
                
                return prefix == abs_directory
            
            def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
            
                for member in tar.getmembers():
                    member_path = os.path.join(path, member.name)
                    if not is_within_directory(path, member_path):
                        raise Exception("Attempted Path Traversal in Tar File")
            
                tar.extractall(path, members, numeric_owner=numeric_owner) 
                
            
            safe_extract(input_tar, tmpdirname)

        os.rename(model_path, os.path.join(os.path.dirname(model_path), 'model_bu.tar.gz'))

        # rewrite config
        conf_path = os.path.join(tmpdirname, 'config.json')
        p = Params.from_file(conf_path, overrides)
        p.to_file(conf_path)

        with tarfile.open(model_path, "w:gz") as output_tar:
            print('Archiving model...')
            output_tar.add(tmpdirname, arcname ="")


if __name__ == "__main__":
    def run_change_config(args):
        assert args.root_dir and args.overrides
        models = Path(args.root_dir).rglob('model.tar.gz')
        for x in models:
            print(x)
            change_config(str(x), args.overrides)

    parse = argparse.ArgumentParser()
    parse.set_defaults(func=run_change_config)
    parse.add_argument("-r", "--root_dir", type=str, help="Source directory with model.tar.gz to modify")
    parse.add_argument("-o", "--overrides", type=str,
                       help='"settings params to override. dictionary, supports nested fieldsby dots')

    args = parse.parse_args()
    args.func(args)