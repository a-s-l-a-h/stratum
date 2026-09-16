python 08_pyi_emit/main.py --input 05_resolve/output_patched --output 08_pyi_emit/output --mode dynamic                                                                   



python 10_embed/emit.py --input 05_resolve/output_patched --static-py 08_pyi_emit/output --setup 00_setup/output/setup_report.json --output 10_embed/output --mode dynamic


python 10_embed/build.py --core 10_embed/output/core --setup 00_setup/output/setup_report.json --nanobind third_party/nanobind --abi arm64-v8a --output 10_embed/output   