# docker build -t gpt-academic-all-capacity -f docs/GithubAction+AllCapacity  --network=host --build-arg http_proxy=http://localhost:10881 --build-arg https_proxy=http://localhost:10881 .

# 从NVIDIA源，从而支持显卡（检查宿主的nvidia-smi中的cuda版本必须>=11.3）
FROM fuqingxu/11.3.1-runtime-ubuntu20.04-with-texlive:latest

# use python3 as the system default python
WORKDIR /gpt
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.8
# 下载pytorch
RUN python3 -m pip install torch --extra-index-url https://download.pytorch.org/whl/cu113
# 准备pip依赖
RUN python3 -m pip install openai numpy arxiv rich
RUN python3 -m pip install colorama Markdown pygments pymupdf
RUN python3 -m pip install python-docx moviepy pdfminer
RUN python3 -m pip install zh_langchain==0.2.1 pypinyin
RUN python3 -m pip install rarfile py7zr
RUN python3 -m pip install aliyun-python-sdk-core==2.13.3 pyOpenSSL scipy git+https://github.com/aliyun/alibabacloud-nls-python-sdk.git
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