

# Run commands for quick look
---

```
python 00_setup/main.py --ndk-path third_party/ndk25/android-ndk-r25c --jar-path third_party/android-35.jar --api-version 35 --ndk-api 24 --python-target-version "3.14.7" --output 00_setup/output
```
---

---

```
python 01_extract/main.py --setup 00_setup/output/setup_report.json --output 01_extract/output
```
---

---

```
python 02_inspect/main.py --input 01_extract/output --output 02_inspect/output
```

---

```
python 03_javap/main.py --input 01_extract/output --targets 02_inspect/targets.json --setup 00_setup/output/setup_report.json --output 03_javap/output
```
---

---

```
python 04_parse/main.py --input 03_javap/output --output 04_parse/output
```

---

---

```
python 04_5_sanitize/main.py --input 04_parse/output --output 04_5_sanitize/output --api-version 35 --strict
```
---

#### (add --strict to also drop restricted/unsupported/conditional hiddenapi members)
#### (add --min-sdk N to also drop members removed at/before API N)

---

### Stage 05 (Pass 1): Initial Resolution
### WITH Stage 04.5:

---

```
python 05_resolve/main.py --input 04_5_sanitize/output --output 05_resolve/output
```
---

### Stage 05.5: Abstract & Interface Adapters Generation 

---

```
python 05_5_abstract/main.py --input 05_resolve/output --output 05_5_abstract/output --mode on
```

---

#### -> Copy 05_5_abstract/output/java/com/stratum/adapters/*.java into Android Studio: runtime module . look the demo project . 

--- 
refer this to find where to place the adapter java files 

https://github.com/a-s-l-a-h/stratum_android_py_3_14

---

### Stage 05 (Pass 2): Final Resolution with Patched Callback Metadata 

---

```
python 05_resolve/main.py --input 05_5_abstract/output/patched --output 05_resolve/output_patched
```

---






---
# stratum embed fully to single .so file it's depending py files all inside to single .so 

### this need same as 00 to 05_5 then 08  ,, and below 10 (10 emit , 10 build) choose static maybe see some perfomance improvment , and also choose --no-log in build when production time

```
python 08_pyi_emit/main.py --input 05_resolve/output_patched --output 08_pyi_emit/output --mode static 

```
```
python 10_embed/emit.py --input 05_resolve/output_patched --static-py 08_pyi_emit/output --setup 00_setup/output/setup_report.json --output 10_embed/output --mode static 
```
```
python 10_embed/build.py --core 10_embed/output/core --setup 00_setup/output/setup_report.json --nanobind third_party/nanobind --abi arm64-v8a --output 10_embed/output --no-log
``` 
---
