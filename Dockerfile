# docker build -t gpt-academic-all-capacity -f docs/GithubAction+AllCapacity  --network=host --build-arg http_proxy=http://localhost:10881 --build-arg https_proxy=http://localhost:10881 .

# 从NVIDIA源，从而支持显卡（检查宿主的nvidia-smi中的cuda版本必须>=11.3）
FROM registry-vpc.cn-hongkong.aliyuncs.com/puerhub/gpt-academic-cuda-texlive-11.3.1:latest

# use python3 as the system default python
WORKDIR /gpt
# 下载分支
#WORKDIR /gpt
#RUN git clone --depth=1 https://github.com/dirname/gpt_academic.git
#WORKDIR /gpt/gpt_academic
COPY . .

RUN python3 -m pip install -r requirements.txt
RUN python3 -m pip install nougat-ocr


# 预热Tiktoken模块
RUN python3  -c 'from check_proxy import warm_up_modules; warm_up_modules()'

# 启动
CMD ["python3", "-u", "main.py"]