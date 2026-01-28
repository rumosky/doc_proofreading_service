import requests

# 测试扣子工作流接口
url = "http://localhost:5000/api/coze/workflow"

try:
    # 测试文本输入
    print("测试文本输入...")
    data = {
        "input": "测试文本"
    }
    
    # 使用流式响应
    response = requests.post(url, data=data, stream=True)
    print(f"状态码: {response.status_code}")
    
    # 处理流式响应
    print("响应内容:")
    for line in response.iter_lines():
        if line:
            line = line.decode('utf-8')
            print(line)
    
    # 关闭响应
    response.close()
    
    # 测试文件上传（如果有测试文件的话）
    # print("\n测试文件上传...")
    # files = {
    #     "file": open("test.docx", "rb")
    # }
    # response = requests.post(url, data=data, files=files, stream=True)
    # print(f"状态码: {response.status_code}")
    # for line in response.iter_lines():
    #     if line:
    #         line = line.decode('utf-8')
    #         print(line)
    # response.close()
    
    print("测试成功！")
except Exception as e:
    print(f"测试失败: {e}")
