import markdown
import importlib
import time
import inspect
import re
import os
import gradio
import shutil
import glob
from latex2mathml.converter import convert as tex2mathml
from functools import wraps, lru_cache
pj = os.path.join

"""
========================================================================
第一部分
函数插件输入输出接驳区
    - ChatBotWithCookies:   带Cookies的Chatbot类，为实现更多强大的功能做基础
    - ArgsGeneralWrapper:   装饰器函数，用于重组输入参数，改变输入参数的顺序与结构
    - update_ui:            刷新界面用 yield from update_ui(chatbot, history)
    - CatchException:       将插件中出的所有问题显示在界面上
    - HotReload:            实现插件的热更新
    - trimmed_format_exc:   打印traceback，为了安全而隐藏绝对地址
========================================================================
"""

class ChatBotWithCookies(list):
    def __init__(self, cookie):
        """
        cookies = {
            'top_p': top_p,
            'temperature': temperature,
            'lock_plugin': bool,
            "files_to_promote": ["file1", "file2"],
            "most_recent_uploaded": {
                "path": "uploaded_path",
                "time": time.time(),
                "time_str": "timestr",
            }
        }
        """
        self._cookies = cookie

    def write_list(self, list):
        for t in list:
            self.append(t)

    def get_list(self):
        return [t for t in self]

    def get_cookies(self):
        return self._cookies


def ArgsGeneralWrapper(f):
    """
    装饰器函数，用于重组输入参数，改变输入参数的顺序与结构。
    """
    def decorated(request: gradio.Request, cookies, max_length, llm_model, txt, txt2, top_p, temperature, chatbot, history, system_prompt, plugin_advanced_arg, *args):
        txt_passon = txt
        if txt == "" and txt2 != "": txt_passon = txt2
        # 引入一个有cookie的chatbot
        cookies.update({
            'top_p':top_p,
            'api_key': cookies['api_key'],
            'llm_model': llm_model,
            'temperature':temperature,
        })
        llm_kwargs = {
            'api_key': cookies['api_key'],
            'llm_model': llm_model,
            'top_p':top_p,
            'max_length': max_length,
            'temperature':temperature,
            'client_ip': request.client.host,
        }
        plugin_kwargs = {
            "advanced_arg": plugin_advanced_arg,
        }
        chatbot_with_cookie = ChatBotWithCookies(cookies)
        chatbot_with_cookie.write_list(chatbot)
        
        if cookies.get('lock_plugin', None) is None:
            # 正常状态
            if len(args) == 0:  # 插件通道
                yield from f(txt_passon, llm_kwargs, plugin_kwargs, chatbot_with_cookie, history, system_prompt, request)
            else:               # 对话通道，或者基础功能通道
                yield from f(txt_passon, llm_kwargs, plugin_kwargs, chatbot_with_cookie, history, system_prompt, *args)
        else:
            # 处理少数情况下的特殊插件的锁定状态
            module, fn_name = cookies['lock_plugin'].split('->')
            f_hot_reload = getattr(importlib.import_module(module, fn_name), fn_name)
            yield from f_hot_reload(txt_passon, llm_kwargs, plugin_kwargs, chatbot_with_cookie, history, system_prompt, request)
            # 判断一下用户是否错误地通过对话通道进入，如果是，则进行提醒
            final_cookies = chatbot_with_cookie.get_cookies()
            # len(args) != 0 代表“提交”键对话通道，或者基础功能通道
            if len(args) != 0 and 'files_to_promote' in final_cookies and len(final_cookies['files_to_promote']) > 0:
                chatbot_with_cookie.append(["检测到**滞留的缓存文档**，请及时处理。", "请及时点击“**保存当前对话**”获取所有滞留文档。"])
                yield from update_ui(chatbot_with_cookie, final_cookies['history'], msg="检测到被滞留的缓存文档")
    return decorated


def update_ui(chatbot, history, msg='正常', **kwargs):  # 刷新界面
    """
    刷新用户界面
    """
    assert isinstance(chatbot, ChatBotWithCookies), "在传递chatbot的过程中不要将其丢弃。必要时, 可用clear将其清空, 然后用for+append循环重新赋值。"
    cookies = chatbot.get_cookies()
    # 备份一份History作为记录
    cookies.update({'history': history})
    # 解决插件锁定时的界面显示问题
    if cookies.get('lock_plugin', None):
        label = cookies.get('llm_model', "") + " | " + "正在锁定插件" + cookies.get('lock_plugin', None)
        chatbot_gr = gradio.update(value=chatbot, label=label)
        if cookies.get('label', "") != label: cookies['label'] = label   # 记住当前的label
    elif cookies.get('label', None):
        chatbot_gr = gradio.update(value=chatbot, label=cookies.get('llm_model', ""))
        cookies['label'] = None    # 清空label
    else:
        chatbot_gr = chatbot

    yield cookies, chatbot_gr, history, msg

def update_ui_lastest_msg(lastmsg, chatbot, history, delay=1):  # 刷新界面
    """
    刷新用户界面
    """
    if len(chatbot) == 0: chatbot.append(["update_ui_last_msg", lastmsg])
    chatbot[-1] = list(chatbot[-1])
    chatbot[-1][-1] = lastmsg
    yield from update_ui(chatbot=chatbot, history=history)
    time.sleep(delay)


def trimmed_format_exc():
    import os, traceback
    str = traceback.format_exc()
    current_path = os.getcwd()
    replace_path = "."
    return str.replace(current_path, replace_path)

def CatchException(f):
    """
    装饰器函数，捕捉函数f中的异常并封装到一个生成器中返回，并显示到聊天当中。
    """

    @wraps(f)
    def decorated(main_input, llm_kwargs, plugin_kwargs, chatbot_with_cookie, history, *args, **kwargs):
        try:
            yield from f(main_input, llm_kwargs, plugin_kwargs, chatbot_with_cookie, history, *args, **kwargs)
        except Exception as e:
            from check_proxy import check_proxy
            from toolbox import get_conf
            proxies, = get_conf('proxies')
            tb_str = '```\n' + trimmed_format_exc() + '```'
            if len(chatbot_with_cookie) == 0:
                chatbot_with_cookie.clear()
                chatbot_with_cookie.append(["插件调度异常", "异常原因"])
            chatbot_with_cookie[-1] = (chatbot_with_cookie[-1][0],
                           f"[Local Message] 实验性函数调用出错: \n\n{tb_str} \n\n当前代理可用性: \n\n{check_proxy(proxies)}")
            yield from update_ui(chatbot=chatbot_with_cookie, history=history, msg=f'异常 {e}') # 刷新界面
    return decorated


def HotReload(f):
    """
    HotReload的装饰器函数，用于实现Python函数插件的热更新。
    函数热更新是指在不停止程序运行的情况下，更新函数代码，从而达到实时更新功能。
    在装饰器内部，使用wraps(f)来保留函数的元信息，并定义了一个名为decorated的内部函数。
    内部函数通过使用importlib模块的reload函数和inspect模块的getmodule函数来重新加载并获取函数模块，
    然后通过getattr函数获取函数名，并在新模块中重新加载函数。
    最后，使用yield from语句返回重新加载过的函数，并在被装饰的函数上执行。
    最终，装饰器函数返回内部函数。这个内部函数可以将函数的原始定义更新为最新版本，并执行函数的新版本。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        fn_name = f.__name__
        f_hot_reload = getattr(importlib.reload(inspect.getmodule(f)), fn_name)
        yield from f_hot_reload(*args, **kwargs)
    return decorated


"""
========================================================================
第二部分
其他小工具:
    - write_history_to_file:    将结果写入markdown文件中
    - regular_txt_to_markdown:  将普通文本转换为Markdown格式的文本。
    - report_execption:         向chatbot中添加简单的意外错误信息
    - text_divide_paragraph:    将文本按照段落分隔符分割开，生成带有段落标签的HTML代码。
    - markdown_convertion:      用多种方式组合，将markdown转化为好看的html
    - format_io:                接管gradio默认的markdown处理方式
    - on_file_uploaded:         处理文件的上传（自动解压）
    - on_report_generated:      将生成的报告自动投射到文件上传区
    - clip_history:             当历史上下文过长时，自动截断
    - get_conf:                 获取设置
    - select_api_key:           根据当前的模型类别，抽取可用的api-key
========================================================================
"""

def get_reduce_token_percent(text):
    """
        * 此函数未来将被弃用
    """
    try:
        # text = "maximum context length is 4097 tokens. However, your messages resulted in 4870 tokens"
        pattern = r"(\d+)\s+tokens\b"
        match = re.findall(pattern, text)
        EXCEED_ALLO = 500  # 稍微留一点余地，否则在回复时会因余量太少出问题
        max_limit = float(match[0]) - EXCEED_ALLO
        current_tokens = float(match[1])
        ratio = max_limit/current_tokens
        assert ratio > 0 and ratio < 1
        return ratio, str(int(current_tokens-max_limit))
    except:
        return 0.5, '不详'


def write_history_to_file(history, file_basename=None, file_fullname=None, auto_caption=True):
    """
    将对话记录history以Markdown格式写入文件中。如果没有指定文件名，则使用当前时间生成文件名。
    """
    import os
    import time
    if file_fullname is None:
        if file_basename is not None:
            file_fullname = pj(get_log_folder(), file_basename)
        else:
            file_fullname = pj(get_log_folder(), f'GPT-Academic-{gen_time_str()}.md')
    os.makedirs(os.path.dirname(file_fullname), exist_ok=True)
    with open(file_fullname, 'w', encoding='utf8') as f:
        f.write('# GPT-Academic Report\n')
        for i, content in enumerate(history):
            try:    
                if type(content) != str: content = str(content)
            except:
                continue
            if i % 2 == 0 and auto_caption:
                f.write('## ')
            try:
                f.write(content)
            except:
                # remove everything that cannot be handled by utf8
                f.write(content.encode('utf-8', 'ignore').decode())
            f.write('\n\n')
    res = os.path.abspath(file_fullname)
    return res


def regular_txt_to_markdown(text):
    """
    将普通文本转换为Markdown格式的文本。
    """
    text = text.replace('\n', '\n\n')
    text = text.replace('\n\n\n', '\n\n')
    text = text.replace('\n\n\n', '\n\n')
    return text




def report_execption(chatbot, history, a, b):
    """
    向chatbot中添加错误信息
    """
    chatbot.append((a, b))
    history.extend([a, b])


def text_divide_paragraph(text):
    """
    将文本按照段落分隔符分割开，生成带有段落标签的HTML代码。
    """
    pre = '<div class="markdown-body">'
    suf = '</div>'
    if text.startswith(pre) and text.endswith(suf):
        return text
    
    if '```' in text:
        # careful input
        return pre + text + suf
    else:
        # wtf input
        lines = text.split("\n")
        for i, line in enumerate(lines):
            lines[i] = lines[i].replace(" ", "&nbsp;")
        text = "</br>".join(lines)
        return pre + text + suf


@lru_cache(maxsize=128) # 使用 lru缓存 加快转换速度
def markdown_convertion(txt):
    """
    将Markdown格式的文本转换为HTML格式。如果包含数学公式，则先将公式转换为HTML格式。
    """
    pre = '<div class="markdown-body">'
    suf = '</div>'
    if txt.startswith(pre) and txt.endswith(suf):
        # print('警告，输入了已经经过转化的字符串，二次转化可能出问题')
        return txt # 已经被转化过，不需要再次转化
    
    markdown_extension_configs = {
        'mdx_math': {
            'enable_dollar_delimiter': True,
            'use_gitlab_delimiters': False,
        },
    }
    find_equation_pattern = r'<script type="math/tex(?:.*?)>(.*?)</script>'

    def tex2mathml_catch_exception(content, *args, **kwargs):
        try:
            content = tex2mathml(content, *args, **kwargs)
        except:
            content = content
        return content

    def replace_math_no_render(match):
        content = match.group(1)
        if 'mode=display' in match.group(0):
            content = content.replace('\n', '</br>')
            return f"<font color=\"#00FF00\">$$</font><font color=\"#FF00FF\">{content}</font><font color=\"#00FF00\">$$</font>"
        else:
            return f"<font color=\"#00FF00\">$</font><font color=\"#FF00FF\">{content}</font><font color=\"#00FF00\">$</font>"

    def replace_math_render(match):
        content = match.group(1)
        if 'mode=display' in match.group(0):
            if '\\begin{aligned}' in content:
                content = content.replace('\\begin{aligned}', '\\begin{array}')
                content = content.replace('\\end{aligned}', '\\end{array}')
                content = content.replace('&', ' ')
            content = tex2mathml_catch_exception(content, display="block")
            return content
        else:
            return tex2mathml_catch_exception(content)

    def markdown_bug_hunt(content):
        """
        解决一个mdx_math的bug（单$包裹begin命令时多余<script>）
        """
        content = content.replace('<script type="math/tex">\n<script type="math/tex; mode=display">', '<script type="math/tex; mode=display">')
        content = content.replace('</script>\n</script>', '</script>')
        return content

    def is_equation(txt):
        """
        判定是否为公式 | 测试1 写出洛伦兹定律，使用tex格式公式 测试2 给出柯西不等式，使用latex格式 测试3 写出麦克斯韦方程组
        """
        if '```' in txt and '```reference' not in txt: return False
        if '$' not in txt and '\\[' not in txt: return False
        mathpatterns = {
            r'(?<!\\|\$)(\$)([^\$]+)(\$)': {'allow_multi_lines': False},                            #  $...$
            r'(?<!\\)(\$\$)([^\$]+)(\$\$)': {'allow_multi_lines': True},                            # $$...$$
            r'(?<!\\)(\\\[)(.+?)(\\\])': {'allow_multi_lines': False},                              # \[...\]
            # r'(?<!\\)(\\\()(.+?)(\\\))': {'allow_multi_lines': False},                            # \(...\)
            # r'(?<!\\)(\\begin{([a-z]+?\*?)})(.+?)(\\end{\2})': {'allow_multi_lines': True},       # \begin...\end
            # r'(?<!\\)(\$`)([^`]+)(`\$)': {'allow_multi_lines': False},                            # $`...`$
        }
        matches = []
        for pattern, property in mathpatterns.items():
            flags = re.ASCII|re.DOTALL if property['allow_multi_lines'] else re.ASCII
            matches.extend(re.findall(pattern, txt, flags))
        if len(matches) == 0: return False
        contain_any_eq = False
        illegal_pattern = re.compile(r'[^\x00-\x7F]|echo')
        for match in matches:
            if len(match) != 3: return False
            eq_canidate = match[1]
            if illegal_pattern.search(eq_canidate): 
                return False
            else: 
                contain_any_eq = True
        return contain_any_eq

    if is_equation(txt):  # 有$标识的公式符号，且没有代码段```的标识
        # convert everything to html format
        split = markdown.markdown(text='---')
        convert_stage_1 = markdown.markdown(text=txt, extensions=['sane_lists', 'tables', 'mdx_math', 'fenced_code'], extension_configs=markdown_extension_configs)
        convert_stage_1 = markdown_bug_hunt(convert_stage_1)
        # 1. convert to easy-to-copy tex (do not render math)
        convert_stage_2_1, n = re.subn(find_equation_pattern, replace_math_no_render, convert_stage_1, flags=re.DOTALL)
        # 2. convert to rendered equation
        convert_stage_2_2, n = re.subn(find_equation_pattern, replace_math_render, convert_stage_1, flags=re.DOTALL)
        # cat them together
        return pre + convert_stage_2_1 + f'{split}' + convert_stage_2_2 + suf
    else:
        return pre + markdown.markdown(txt, extensions=['sane_lists', 'tables', 'fenced_code', 'codehilite']) + suf


def close_up_code_segment_during_stream(gpt_reply):
    """
    在gpt输出代码的中途（输出了前面的```，但还没输出完后面的```），补上后面的```

    Args:
        gpt_reply (str): GPT模型返回的回复字符串。

    Returns:
        str: 返回一个新的字符串，将输出代码片段的“后面的```”补上。

    """
    if '```' not in gpt_reply:
        return gpt_reply
    if gpt_reply.endswith('```'):
        return gpt_reply

    # 排除了以上两个情况，我们
    segments = gpt_reply.split('```')
    n_mark = len(segments) - 1
    if n_mark % 2 == 1:
        # print('输出代码片段中！')
        return gpt_reply+'\n```'
    else:
        return gpt_reply


def format_io(self, y):
    """
    将输入和输出解析为HTML格式。将y中最后一项的输入部分段落化，并将输出部分的Markdown和数学公式转换为HTML格式。
    """
    if y is None or y == []:
        return []
    i_ask, gpt_reply = y[-1]
    # 输入部分太自由，预处理一波
    if i_ask is not None: i_ask = text_divide_paragraph(i_ask)
    # 当代码输出半截的时候，试着补上后个```
    if gpt_reply is not None: gpt_reply = close_up_code_segment_during_stream(gpt_reply)
    # process
    y[-1] = (
        None if i_ask is None else markdown.markdown(i_ask, extensions=['fenced_code', 'tables']),
        None if gpt_reply is None else markdown_convertion(gpt_reply)
    )
    return y


def find_free_port():
    """
    返回当前系统中可用的未使用端口。
    """
    import socket
    from contextlib import closing
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def extract_archive(file_path, dest_dir):
    import zipfile
    import tarfile
    import os
    # Get the file extension of the input file
    file_extension = os.path.splitext(file_path)[1]

    # Extract the archive based on its extension
    if file_extension == '.zip':
        with zipfile.ZipFile(file_path, 'r') as zipobj:
            zipobj.extractall(path=dest_dir)
            print("Successfully extracted zip archive to {}".format(dest_dir))

    elif file_extension in ['.tar', '.gz', '.bz2']:
        with tarfile.open(file_path, 'r:*') as tarobj:
            tarobj.extractall(path=dest_dir)
            print("Successfully extracted tar archive to {}".format(dest_dir))

    # 第三方库，需要预先pip install rarfile
    # 此外，Windows上还需要安装winrar软件，配置其Path环境变量，如"C:\Program Files\WinRAR"才可以
    elif file_extension == '.rar':
        try:
            import rarfile
            with rarfile.RarFile(file_path) as rf:
                rf.extractall(path=dest_dir)
                print("Successfully extracted rar archive to {}".format(dest_dir))
        except:
            print("Rar format requires additional dependencies to install")
            return '\n\n解压失败! 需要安装pip install rarfile来解压rar文件。建议：使用zip压缩格式。'

    # 第三方库，需要预先pip install py7zr
    elif file_extension == '.7z':
        try:
            import py7zr
            with py7zr.SevenZipFile(file_path, mode='r') as f:
                f.extractall(path=dest_dir)
                print("Successfully extracted 7z archive to {}".format(dest_dir))
        except:
            print("7z format requires additional dependencies to install")
            return '\n\n解压失败! 需要安装pip install py7zr来解压7z文件'
    else:
        return ''
    return ''


def find_recent_files(directory):
    """
        me: find files that is created with in one minutes under a directory with python, write a function
        gpt: here it is!
    """
    import os
    import time
    current_time = time.time()
    one_minute_ago = current_time - 60
    recent_files = []
    if not os.path.exists(directory): 
        os.makedirs(directory, exist_ok=True)
    for filename in os.listdir(directory):
        file_path = pj(directory, filename)
        if file_path.endswith('.log'):
            continue
        created_time = os.path.getmtime(file_path)
        if created_time >= one_minute_ago:
            if os.path.isdir(file_path):
                continue
            recent_files.append(file_path)

    return recent_files

def promote_file_to_downloadzone(file, rename_file=None, chatbot=None):
    # 将文件复制一份到下载区
    import shutil
    if rename_file is None: rename_file = f'{gen_time_str()}-{os.path.basename(file)}'
    new_path = pj(get_log_folder(), rename_file)
    # 如果已经存在，先删除
    if os.path.exists(new_path) and not os.path.samefile(new_path, file): os.remove(new_path)
    # 把文件复制过去
    if not os.path.exists(new_path): shutil.copyfile(file, new_path)
    # 将文件添加到chatbot cookie中，避免多用户干扰
    if chatbot is not None:
        if 'files_to_promote' in chatbot._cookies: current = chatbot._cookies['files_to_promote']
        else: current = []
        chatbot._cookies.update({'files_to_promote': [new_path] + current})
    return new_path

def disable_auto_promotion(chatbot):
    chatbot._cookies.update({'files_to_promote': []})
    return

def is_the_upload_folder(string):
    PATH_PRIVATE_UPLOAD, = get_conf('PATH_PRIVATE_UPLOAD')
    pattern = r'^PATH_PRIVATE_UPLOAD/[A-Za-z0-9_-]+/\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$'
    pattern = pattern.replace('PATH_PRIVATE_UPLOAD', PATH_PRIVATE_UPLOAD)
    if re.match(pattern, string): return True
    else: return False

def del_outdated_uploads(outdate_time_seconds):
    PATH_PRIVATE_UPLOAD, = get_conf('PATH_PRIVATE_UPLOAD')
    current_time = time.time()
    one_hour_ago = current_time - outdate_time_seconds
    # Get a list of all subdirectories in the PATH_PRIVATE_UPLOAD folder
    # Remove subdirectories that are older than one hour
    for subdirectory in glob.glob(f'{PATH_PRIVATE_UPLOAD}/*/*'):
        subdirectory_time = os.path.getmtime(subdirectory)
        if subdirectory_time < one_hour_ago:
            try: shutil.rmtree(subdirectory)
            except: pass
    return

def on_file_uploaded(request: gradio.Request, files, chatbot, txt, txt2, checkboxes, cookies):
    """
    当文件被上传时的回调函数
    """
    if len(files) == 0:
        return chatbot, txt
    
    # 移除过时的旧文件从而节省空间&保护隐私
    outdate_time_seconds = 60
    del_outdated_uploads(outdate_time_seconds)

    # 创建工作路径
    user_name = "default" if not request.username else request.username
    time_tag = gen_time_str()
    PATH_PRIVATE_UPLOAD, = get_conf('PATH_PRIVATE_UPLOAD')
    target_path_base = pj(PATH_PRIVATE_UPLOAD, user_name, time_tag)
    os.makedirs(target_path_base, exist_ok=True)

    # 逐个文件转移到目标路径
    upload_msg = ''
    for file in files:
        file_origin_name = os.path.basename(file.orig_name)
        this_file_path = pj(target_path_base, file_origin_name)
        shutil.move(file.name, this_file_path)
        upload_msg += extract_archive(file_path=this_file_path, dest_dir=this_file_path+'.extract')
    
    # 整理文件集合
    moved_files = [fp for fp in glob.glob(f'{target_path_base}/**/*', recursive=True)]
    if "浮动输入区" in checkboxes: 
        txt, txt2 = "", target_path_base
    else:
        txt, txt2 = target_path_base, ""

    # 输出消息
    moved_files_str = '\t\n\n'.join(moved_files)
    chatbot.append(['我上传了文件，请查收', 
                    f'[Local Message] 收到以下文件: \n\n{moved_files_str}' +
                    f'\n\n调用路径参数已自动修正到: \n\n{txt}' +
                    f'\n\n现在您点击任意函数插件时，以上文件将被作为输入参数'+upload_msg])
    
    # 记录近期文件
    cookies.update({
        'most_recent_uploaded': {
            'path': target_path_base,
            'time': time.time(),
            'time_str': time_tag
    }})
    return chatbot, txt, txt2, cookies


def on_report_generated(cookies, files, chatbot):
    from toolbox import find_recent_files
    PATH_LOGGING, = get_conf('PATH_LOGGING')
    if 'files_to_promote' in cookies:
        report_files = cookies['files_to_promote']
        cookies.pop('files_to_promote')
    else:
        report_files = find_recent_files(PATH_LOGGING)
    if len(report_files) == 0:
        return cookies, None, chatbot
    # files.extend(report_files)
    file_links = ''
    for f in report_files: file_links += f'<br/><a href="file={os.path.abspath(f)}" target="_blank">{f}</a>'
    chatbot.append(['报告如何远程获取？', f'报告已经添加到右侧“文件上传区”（可能处于折叠状态），请查收。{file_links}'])
    return cookies, report_files, chatbot

def load_chat_cookies():
    API_KEY, LLM_MODEL, AZURE_API_KEY = get_conf('API_KEY', 'LLM_MODEL', 'AZURE_API_KEY')
    if is_any_api_key(AZURE_API_KEY):
        if is_any_api_key(API_KEY): API_KEY = API_KEY + ',' + AZURE_API_KEY
        else: API_KEY = AZURE_API_KEY
    return {'api_key': API_KEY, 'llm_model': LLM_MODEL}

def is_openai_api_key(key):
    CUSTOM_API_KEY_PATTERN, = get_conf('CUSTOM_API_KEY_PATTERN')
    if len(CUSTOM_API_KEY_PATTERN) != 0:
        API_MATCH_ORIGINAL = re.match(CUSTOM_API_KEY_PATTERN, key)
    else:
        API_MATCH_ORIGINAL = re.match(r"sk-[a-zA-Z0-9]{48}$", key)
    return bool(API_MATCH_ORIGINAL)

def is_azure_api_key(key):
    API_MATCH_AZURE = re.match(r"[a-zA-Z0-9]{32}$", key)
    return bool(API_MATCH_AZURE)

def is_api2d_key(key):
    API_MATCH_API2D = re.match(r"fk[a-zA-Z0-9]{6}-[a-zA-Z0-9]{32}$", key)
    return bool(API_MATCH_API2D)

def is_any_api_key(key):
    if ',' in key:
        keys = key.split(',')
        for k in keys:
            if is_any_api_key(k): return True
        return False
    else:
        return is_openai_api_key(key) or is_api2d_key(key) or is_azure_api_key(key)

def what_keys(keys):
    avail_key_list = {'OpenAI Key':0, "Azure Key":0, "API2D Key":0}
    key_list = keys.split(',')

    for k in key_list:
        if is_openai_api_key(k): 
            avail_key_list['OpenAI Key'] += 1

    for k in key_list:
        if is_api2d_key(k): 
            avail_key_list['API2D Key'] += 1

    for k in key_list:
        if is_azure_api_key(k): 
            avail_key_list['Azure Key'] += 1

    return f"检测到： PuerHub AI 令牌 {avail_key_list['OpenAI Key']} 个, Azure Key {avail_key_list['Azure Key']} 个, API2D Key {avail_key_list['API2D Key']} 个"

def select_api_key(keys, llm_model):
    import random
    avail_key_list = []
    key_list = keys.split(',')

    if llm_model.startswith('gpt-') or llm_model.startswith('claude-'):
        for k in key_list:
            if is_openai_api_key(k): avail_key_list.append(k)

    if llm_model.startswith('api2d-'):
        for k in key_list:
            if is_api2d_key(k): avail_key_list.append(k)

    if llm_model.startswith('azure-'):
        for k in key_list:
            if is_azure_api_key(k): avail_key_list.append(k)

    if len(avail_key_list) == 0:
        raise RuntimeError(f"您提供的 PuerHub AI 令牌不满足要求, 无法使用{llm_model}! 这可能是 PuerHub AI 不支持该模型或您使用了错误的 PuerHub API 令牌请重新到 [点击这里](https://ai.puerhub.xyz/token) 生成令牌 🔑")

    api_key = random.choice(avail_key_list) # 随机负载均衡
    return api_key

def read_env_variable(arg, default_value):
    """
    环境变量可以是 `GPT_ACADEMIC_CONFIG`(优先)，也可以直接是`CONFIG`
    例如在windows cmd中，既可以写：
        set USE_PROXY=True
        set API_KEY=sk-j7caBpkRoxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        set proxies={"http":"http://127.0.0.1:10085", "https":"http://127.0.0.1:10085",}
        set AVAIL_LLM_MODELS=["gpt-3.5-turbo", "chatglm"]
        set AUTHENTICATION=[("username", "password"), ("username2", "password2")]
    也可以写：
        set GPT_ACADEMIC_USE_PROXY=True
        set GPT_ACADEMIC_API_KEY=sk-j7caBpkRoxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        set GPT_ACADEMIC_proxies={"http":"http://127.0.0.1:10085", "https":"http://127.0.0.1:10085",}
        set GPT_ACADEMIC_AVAIL_LLM_MODELS=["gpt-3.5-turbo", "chatglm"]
        set GPT_ACADEMIC_AUTHENTICATION=[("username", "password"), ("username2", "password2")]
    """
    from colorful import print亮红, print亮绿
    arg_with_prefix = "GPT_ACADEMIC_" + arg 
    if arg_with_prefix in os.environ: 
        env_arg = os.environ[arg_with_prefix]
    elif arg in os.environ: 
        env_arg = os.environ[arg]
    else:
        raise KeyError
    print(f"[ENV_VAR] 尝试加载{arg}，默认值：{default_value} --> 修正值：{env_arg}")
    try:
        if isinstance(default_value, bool):
            env_arg = env_arg.strip()
            if env_arg == 'True': r = True
            elif env_arg == 'False': r = False
            else: print('enter True or False, but have:', env_arg); r = default_value
        elif isinstance(default_value, int):
            r = int(env_arg)
        elif isinstance(default_value, float):
            r = float(env_arg)
        elif isinstance(default_value, str):
            r = env_arg.strip()
        elif isinstance(default_value, dict):
            r = eval(env_arg)
        elif isinstance(default_value, list):
            r = eval(env_arg)
        elif default_value is None:
            assert arg == "proxies"
            r = eval(env_arg)
        else:
            print亮红(f"[ENV_VAR] 环境变量{arg}不支持通过环境变量设置! ")
            raise KeyError
    except:
        print亮红(f"[ENV_VAR] 环境变量{arg}加载失败! ")
        raise KeyError(f"[ENV_VAR] 环境变量{arg}加载失败! ")

    print亮绿(f"[ENV_VAR] 成功读取环境变量{arg}")
    return r

@lru_cache(maxsize=128)
def read_single_conf_with_lru_cache(arg):
    from colorful import print亮红, print亮绿, print亮蓝
    try:
        # 优先级1. 获取环境变量作为配置
        default_ref = getattr(importlib.import_module('config'), arg)   # 读取默认值作为数据类型转换的参考
        r = read_env_variable(arg, default_ref) 
    except:
        try:
            # 优先级2. 获取config_private中的配置
            r = getattr(importlib.import_module('config_private'), arg)
        except:
            # 优先级3. 获取config中的配置
            r = getattr(importlib.import_module('config'), arg)

    # 在读取API_KEY时，检查一下是不是忘了改config
    if arg == 'API_KEY':
        print亮蓝(f"[API_KEY] 本项目现已支持OpenAI和Azure的api-key。也支持同时填写多个api-key，如API_KEY=\"openai-key1,openai-key2,azure-key3\"")
        print亮蓝(f"[API_KEY] 您既可以在config.py中修改api-key(s)，也可以在问题输入区输入临时的api-key(s)，然后回车键提交后即可生效。")
        if is_any_api_key(r):
            print亮绿(f"[API_KEY] 您的 API_KEY 是: {r[:15]}*** API_KEY 导入成功")
        else:
            print亮红( "[API_KEY] 您的 API_KEY 不满足任何一种已知的密钥格式，请在config文件中修改API密钥之后再运行。")
    if arg == 'proxies':
        if not read_single_conf_with_lru_cache('USE_PROXY'): r = None   # 检查USE_PROXY，防止proxies单独起作用
        if r is None:
            print亮红('[PROXY] 网络代理状态：未配置。无代理状态下很可能无法访问OpenAI家族的模型。建议：检查USE_PROXY选项是否修改。')
        else:
            print亮绿('[PROXY] 网络代理状态：已配置。配置信息如下：', r)
            assert isinstance(r, dict), 'proxies格式错误，请注意proxies选项的格式，不要遗漏括号。'
    return r


@lru_cache(maxsize=128)
def get_conf(*args):
    # 建议您复制一个config_private.py放自己的秘密, 如API和代理网址, 避免不小心传github被别人看到
    res = []
    for arg in args:
        r = read_single_conf_with_lru_cache(arg)
        res.append(r)
    return res


def clear_line_break(txt):
    txt = txt.replace('\n', ' ')
    txt = txt.replace('  ', ' ')
    txt = txt.replace('  ', ' ')
    return txt


class DummyWith():
    """
    这段代码定义了一个名为DummyWith的空上下文管理器，
    它的作用是……额……就是不起作用，即在代码结构不变得情况下取代其他的上下文管理器。
    上下文管理器是一种Python对象，用于与with语句一起使用，
    以确保一些资源在代码块执行期间得到正确的初始化和清理。
    上下文管理器必须实现两个方法，分别为 __enter__()和 __exit__()。
    在上下文执行开始的情况下，__enter__()方法会在代码块被执行前被调用，
    而在上下文执行结束时，__exit__()方法则会被调用。
    """
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return

def run_gradio_in_subpath(demo, auth, port, custom_path):
    """
    把gradio的运行地址更改到指定的二次路径上
    """
    def is_path_legal(path: str)->bool:
        '''
        check path for sub url
        path: path to check
        return value: do sub url wrap
        '''
        if path == "/": return True
        if len(path) == 0:
            print("ilegal custom path: {}\npath must not be empty\ndeploy on root url".format(path))
            return False
        if path[0] == '/':
            if path[1] != '/':
                print("deploy on sub-path {}".format(path))
                return True
            return False
        print("ilegal custom path: {}\npath should begin with \'/\'\ndeploy on root url".format(path))
        return False

    if not is_path_legal(custom_path): raise RuntimeError('Ilegal custom path')
    import uvicorn
    import gradio as gr
    from fastapi import FastAPI
    app = FastAPI()
    if custom_path != "/":
        @app.get("/")
        def read_main(): 
            return {"message": f"Gradio is running at: {custom_path}"}
    app = gr.mount_gradio_app(app, demo, path=custom_path)
    uvicorn.run(app, host="0.0.0.0", port=port) # , auth=auth


def clip_history(inputs, history, tokenizer, max_token_limit):
    """
    reduce the length of history by clipping.
    this function search for the longest entries to clip, little by little,
    until the number of token of history is reduced under threshold.
    通过裁剪来缩短历史记录的长度。 
    此函数逐渐地搜索最长的条目进行剪辑，
    直到历史记录的标记数量降低到阈值以下。
    """
    import numpy as np
    from request_llm.bridge_all import model_info
    def get_token_num(txt): 
        return len(tokenizer.encode(txt, disallowed_special=()))
    input_token_num = get_token_num(inputs)
    if input_token_num < max_token_limit * 3 / 4:
        # 当输入部分的token占比小于限制的3/4时，裁剪时
        # 1. 把input的余量留出来
        max_token_limit = max_token_limit - input_token_num
        # 2. 把输出用的余量留出来
        max_token_limit = max_token_limit - 128
        # 3. 如果余量太小了，直接清除历史
        if max_token_limit < 128:
            history = []
            return history
    else:
        # 当输入部分的token占比 > 限制的3/4时，直接清除历史
        history = []
        return history

    everything = ['']
    everything.extend(history)
    n_token = get_token_num('\n'.join(everything))
    everything_token = [get_token_num(e) for e in everything]

    # 截断时的颗粒度
    delta = max(everything_token) // 16

    while n_token > max_token_limit:
        where = np.argmax(everything_token)
        encoded = tokenizer.encode(everything[where], disallowed_special=())
        clipped_encoded = encoded[:len(encoded)-delta]
        everything[where] = tokenizer.decode(clipped_encoded)[:-1]    # -1 to remove the may-be illegal char
        everything_token[where] = get_token_num(everything[where])
        n_token = get_token_num('\n'.join(everything))

    history = everything[1:]
    return history

"""
========================================================================
第三部分
其他小工具:
    - zip_folder:    把某个路径下所有文件压缩，然后转移到指定的另一个路径中（gpt写的）
    - gen_time_str:  生成时间戳
    - ProxyNetworkActivate: 临时地启动代理网络（如果有）
    - objdump/objload: 快捷的调试函数
========================================================================
"""

def zip_folder(source_folder, dest_folder, zip_name):
    import zipfile
    import os
    # Make sure the source folder exists
    if not os.path.exists(source_folder):
        print(f"{source_folder} does not exist")
        return

    # Make sure the destination folder exists
    if not os.path.exists(dest_folder):
        print(f"{dest_folder} does not exist")
        return

    # Create the name for the zip file
    zip_file = pj(dest_folder, zip_name)

    # Create a ZipFile object
    with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Walk through the source folder and add files to the zip file
        for foldername, subfolders, filenames in os.walk(source_folder):
            for filename in filenames:
                filepath = pj(foldername, filename)
                zipf.write(filepath, arcname=os.path.relpath(filepath, source_folder))

    # Move the zip file to the destination folder (if it wasn't already there)
    if os.path.dirname(zip_file) != dest_folder:
        os.rename(zip_file, pj(dest_folder, os.path.basename(zip_file)))
        zip_file = pj(dest_folder, os.path.basename(zip_file))

    print(f"Zip file created at {zip_file}")

def zip_result(folder):
    t = gen_time_str()
    zip_folder(folder, get_log_folder(), f'{t}-result.zip')
    return pj(get_log_folder(), f'{t}-result.zip')

def gen_time_str():
    import time
    return time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())

def get_log_folder(user='default', plugin_name='shared'):
    PATH_LOGGING, = get_conf('PATH_LOGGING')
    _dir = pj(PATH_LOGGING, user, plugin_name)
    if not os.path.exists(_dir): os.makedirs(_dir)
    return _dir

class ProxyNetworkActivate():
    """
    这段代码定义了一个名为TempProxy的空上下文管理器, 用于给一小段代码上代理
    """
    def __init__(self, task=None) -> None:
        self.task = task
        if not task:
            # 不给定task, 那么我们默认代理生效
            self.valid = True
        else:
            # 给定了task, 我们检查一下
            from toolbox import get_conf
            WHEN_TO_USE_PROXY, = get_conf('WHEN_TO_USE_PROXY')
            self.valid = (task in WHEN_TO_USE_PROXY)

    def __enter__(self):
        if not self.valid: return self
        from toolbox import get_conf
        proxies, = get_conf('proxies')
        if 'no_proxy' in os.environ: os.environ.pop('no_proxy')
        if proxies is not None:
            if 'http' in proxies: os.environ['HTTP_PROXY'] = proxies['http']
            if 'https' in proxies: os.environ['HTTPS_PROXY'] = proxies['https']
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        os.environ['no_proxy'] = '*'
        if 'HTTP_PROXY' in os.environ: os.environ.pop('HTTP_PROXY')
        if 'HTTPS_PROXY' in os.environ: os.environ.pop('HTTPS_PROXY')
        return

def objdump(obj, file='objdump.tmp'):
    import pickle
    with open(file, 'wb+') as f:
        pickle.dump(obj, f)
    return

def objload(file='objdump.tmp'):
    import pickle, os
    if not os.path.exists(file): 
        return
    with open(file, 'rb') as f:
        return pickle.load(f)
    
def Singleton(cls):
    """
    一个单实例装饰器
    """
    _instance = {}
 
    def _singleton(*args, **kargs):
        if cls not in _instance:
            _instance[cls] = cls(*args, **kargs)
        return _instance[cls]
 
    return _singleton

"""
========================================================================
第四部分
接驳虚空终端:
    - set_conf:                     在运行过程中动态地修改配置
    - set_multi_conf:               在运行过程中动态地修改多个配置
    - get_plugin_handle:            获取插件的句柄
    - get_plugin_default_kwargs:    获取插件的默认参数
    - get_chat_handle:              获取简单聊天的句柄
    - get_chat_default_kwargs:      获取简单聊天的默认参数
========================================================================
"""

def set_conf(key, value):
    from toolbox import read_single_conf_with_lru_cache, get_conf
    read_single_conf_with_lru_cache.cache_clear()
    get_conf.cache_clear()
    os.environ[key] = str(value)
    altered, = get_conf(key)
    return altered

def set_multi_conf(dic):
    for k, v in dic.items(): set_conf(k, v)
    return

def get_plugin_handle(plugin_name):
    """
    e.g. plugin_name = 'crazy_functions.批量Markdown翻译->Markdown翻译指定语言'
    """
    import importlib
    assert '->' in plugin_name, \
        "Example of plugin_name: crazy_functions.批量Markdown翻译->Markdown翻译指定语言"
    module, fn_name = plugin_name.split('->')
    f_hot_reload = getattr(importlib.import_module(module, fn_name), fn_name)
    return f_hot_reload

def get_chat_handle():
    """
    """
    from request_llm.bridge_all import predict_no_ui_long_connection
    return predict_no_ui_long_connection

def get_plugin_default_kwargs():
    """
    """
    from toolbox import get_conf, ChatBotWithCookies

    WEB_PORT, LLM_MODEL, API_KEY = \
        get_conf('WEB_PORT', 'LLM_MODEL', 'API_KEY')

    llm_kwargs = {
        'api_key': API_KEY,
        'llm_model': LLM_MODEL,
        'top_p':1.0, 
        'max_length': None,
        'temperature':1.0,
    }
    chatbot = ChatBotWithCookies(llm_kwargs)

    # txt, llm_kwargs, plugin_kwargs, chatbot, history, system_prompt, web_port
    DEFAULT_FN_GROUPS_kwargs = {
        "main_input": "./README.md",
        "llm_kwargs": llm_kwargs,
        "plugin_kwargs": {},
        "chatbot_with_cookie": chatbot,
        "history": [],
        "system_prompt": "You are a good AI.", 
        "web_port": WEB_PORT
    }
    return DEFAULT_FN_GROUPS_kwargs

def get_chat_default_kwargs():
    """
    """
    from toolbox import get_conf

    LLM_MODEL, API_KEY = get_conf('LLM_MODEL', 'API_KEY')

    llm_kwargs = {
        'api_key': API_KEY,
        'llm_model': LLM_MODEL,
        'top_p':1.0, 
        'max_length': None,
        'temperature':1.0,
    }

    default_chat_kwargs = {
        "inputs": "Hello there, are you ready?",
        "llm_kwargs": llm_kwargs,
        "history": [],
        "sys_prompt": "You are AI assistant",
        "observe_window": None,
        "console_slience": False,
    }

    return default_chat_kwargs

