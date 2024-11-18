import os
import uuid
import subprocess
import requests
from flask import Flask, render_template, jsonify, request
from datetime import datetime
from PIL import Image
import base64
from io import BytesIO
import socket
import threading
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import time

app = Flask(__name__)

# Supabase API 設定
supabase_url = "https://icubopeuonposdinrtsf.supabase.co/rest/v1/machine_code"
api_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImljdWJvcGV1b25wb3NkaW5ydHNmIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTY4OTkyMjIyMSwiZXhwIjoyMDA1NDk4MjIxfQ.IV0z-vc5qqr3cmzSAe5M-9aF23mn386FC1FDpg_oddo"
headers = {
    "apikey": api_key,
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# 設備 IP 和通訊密碼
password = "@han97260403"
DEVICE_PORT = 8090  # 新增固定端口

# 最大圖片尺寸
MAX_WIDTH = 800
MAX_HEIGHT = 800
MAX_FILE_SIZE = 400 * 1024  # 400KB

# 配置 requests 的重試機制
retry_strategy = Retry(
    total=3,  # 最多重試3次
    backoff_factor=1,  # 重試間隔時間
    status_forcelist=[500, 502, 503, 504]  # 這些狀態碼會觸發重試
)
http_adapter = HTTPAdapter(max_retries=retry_strategy)
session = requests.Session()
session.mount("http://", http_adapter)
session.mount("https://", http_adapter)

def find_device_ip():
    """掃描網絡尋找設備 IP"""
    def check_ip(ip):
        try:
            url = f"http://{ip}:{DEVICE_PORT}/getDeviceKey"
            response = requests.post(url, data={"pass": password}, timeout=2)
            if response.status_code == 200:
                return ip
        except:
            pass
        return None

    # 獲取本機 IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()

    # 獲取網段
    network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
    
    # 使用線程池加速掃描
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_ip, str(ip)) for ip in network.hosts()]
        for future in as_completed(futures):
            result = future.result()
            if result:
                return f"http://{result}:{DEVICE_PORT}"
    
    return None

def get_device_ip():
    """獲取設備 IP 並緩存結果"""
    if not hasattr(get_device_ip, "cached_ip") or not get_device_ip.cached_ip:
        get_device_ip.cached_ip = find_device_ip()
    return get_device_ip.cached_ip

def get_serial_number():
    """從人臉辨識設備獲取序列號"""
    try:
        device_ip = get_device_ip()
        if not device_ip:
            print("無法找到設備 IP")
            return None
            
        url = f"{device_ip}/getDeviceKey"
        data = {
            "pass": password
        }
        
        response = requests.post(url, data=data, timeout=5)
        
        if response.status_code == 200:
            try:
                device_info = response.json()
                serial_number = device_info.get('data')
                if serial_number:
                    print(f"找到序列號: {serial_number}")
                    return serial_number
                else:
                    print("未找到序列號")
                    return None
            except Exception as e:
                print(f"解析 JSON 時發生錯誤: {str(e)}")
                return None
        else:
            print(f"獲取設備信息失敗: {response.text}")
            return None
    except Exception as e:
        print(f"獲取序列號時發生錯誤: {str(e)}")
        return None

def resize_image(image):
    """縮放圖片，確保足夠大的尺寸以識別臉部"""
    # 設定最小尺寸
    MIN_WIDTH = 640
    MIN_HEIGHT = 480
    
    width, height = image.size
    
    # 如果圖片太小，放大到最小尺寸
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        ratio = max(MIN_WIDTH/width, MIN_HEIGHT/height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # 如果圖片太大，按照原來的最大尺寸限制
    if width > MAX_WIDTH or height > MAX_HEIGHT:
        image.thumbnail((MAX_WIDTH, MAX_HEIGHT))
    
    return image

def register_face_until_success(person_id, img_base64, image_path):
    """註冊人臉，失敗後旋轉圖片並重試，最多旋轉4次"""
    device_ip = get_device_ip()
    if not device_ip:
        return {"success": False, "error": "無法找到設備 IP"}
        
    max_attempts = 4
    attempts = 0
    
    while attempts < max_attempts:
        url = f"{device_ip}/face/create"
        face_data = {
            "pass": password,
            "personId": person_id,
            "imgBase64": img_base64
        }
        try:
            response = requests.post(url, data=face_data)
            response.raise_for_status()
            
            result = response.json()
            if result.get("success"):
                print(f"人臉註冊成功, personId: {person_id}")
                return {"success": True}
            else:
                print(f"人臉註冊失敗, 嘗試旋轉圖片 {attempts + 1} 次: {person_id}")
                print(f"錯誤信息: {result}")
                # 旋轉圖片 90 度並更新 Base64
                image = Image.open(image_path)
                image = image.rotate(-90, expand=True)
                image.save(image_path)
                with open(image_path, "rb") as image_file:
                    img_base64 = base64.b64encode(image_file.read()).decode()
                attempts += 1
        except Exception as e:
            print(f"請求錯誤: {e}")
            return {"success": False, "error": str(e)}

    print(f"人臉註冊失敗，已達到最大嘗試次數: {person_id}")
    return {"success": False, "error": "達到最大嘗試次數"}

def update_machine_info(name, name_id, photo, rotation):
    """更新機器資訊到Supabase並註冊人臉"""
    temp_path = None
    try:
        # 獲取序列號
        serial_number = get_serial_number()
        if not serial_number:
            print("序列號獲取失敗")
            return {"success": False, "error": "無法獲取設備序列號"}
            
        # 處理圖片
        try:
            # 保存臨時文件
            temp_path = f"temp_{name_id}.jpg"
            image = Image.open(photo)
            print(f"原始圖片大小: {image.size}")
            
            # 先旋轉再調整大小
            if rotation:
                image = image.rotate(-int(rotation), expand=True)
            image = resize_image(image)
            print(f"調整後圖片大小: {image.size}")
            
            # 保存處理後的圖片
            image.save(temp_path, quality=95)
            
            # 轉換為 base64
            with open(temp_path, "rb") as image_file:
                img_base64 = base64.b64encode(image_file.read()).decode()
            
            print(f"Base64 圖片大小: {len(img_base64)} 字節")
                
        except Exception as e:
            print(f"圖片處理錯誤: {str(e)}")
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            return {"success": False, "error": f"圖片處理失敗: {str(e)}"}
        
        # 新增人員
        print(f"開始新增人員: {name} ({name_id})")
        person_result = add_person(name, name_id)
        if not person_result["success"]:
            print(f"新增人員失敗: {person_result.get('error')}")
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            return {"success": False, "error": f"新增人員失敗: {person_result.get('error')}"}
            
        # 註冊人臉
        print("開始註冊人臉")
        face_result = register_face_until_success(name_id, img_base64, temp_path)
        if not face_result["success"]:
            print(f"註冊人臉失敗: {face_result.get('error')}")
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            return {"success": False, "error": f"註冊人臉失敗: {face_result.get('error')}"}
            
        # 更新到 Supabase
        print("開始更新到 Supabase")
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = {
            'code': serial_number,
            'name': name,
            'nameid': name_id,
            'created_at': current_time,
            'photo': img_base64
        }
        
        # 使用重試機制發送請求
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                response = session.post(supabase_url, json=data, headers=headers, timeout=10)
                print(f"Supabase 回應狀態碼: {response.status_code}")
                print(f"Supabase 回應內容: {response.text}")
                
                if response.status_code == 201:
                    print("成功更新到 Supabase")
                    return {
                        "success": True, 
                        "serial_number": serial_number,
                        "name": name,
                        "name_id": name_id
                    }
                else:
                    print(f"Supabase 更新失敗: {response.text}")
                    if attempt < max_attempts - 1:
                        print(f"等待後重試... (嘗試 {attempt + 2}/{max_attempts})")
                        time.sleep(2)  # 等待2秒後重試
                    else:
                        return {"success": False, "error": f"Supabase error: {response.text}"}
                        
            except requests.exceptions.RequestException as e:
                print(f"請求異常: {str(e)}")
                if attempt < max_attempts - 1:
                    print(f"等待後重試... (嘗試 {attempt + 2}/{max_attempts})")
                    time.sleep(2)
                else:
                    return {"success": False, "error": f"網絡連接錯誤: {str(e)}"}
    
    except Exception as e:
        print(f"整體處理過程發生錯誤: {str(e)}")
        return {"success": False, "error": str(e)}
    
    finally:
        # 確保清理臨時文件
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                print(f"清理臨時文件失敗: {str(e)}")

def add_person(name, person_id):
    """新增人員"""
    device_ip = get_device_ip()
    if not device_ip:
        return {"success": False, "error": "無法找到設備 IP"}
        
    url = f"{device_ip}/person/create"
    person_data = {
        "pass": password,
        "person": f'{{"id":"{person_id}", "name":"{name}"}}'
    }
    try:
        print(f"發送新增人員請求: {person_data}")
        response = requests.post(url, data=person_data)
        print(f"新增人員回應狀態碼: {response.status_code}")
        print(f"新增人員回應內容: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                print(f"成功新增人員: {name} ({person_id})")
                return {"success": True}
            else:
                error_msg = result.get("message", "Unknown error")
                print(f"新增人員失敗: {error_msg}")
                return {"success": False, "error": error_msg}
        else:
            print(f"新增人員請求失敗，狀態碼: {response.status_code}")
            return {"success": False, "error": f"Request failed with status code: {response.status_code}"}
    except Exception as e:
        print(f"新增人員時發生錯誤: {str(e)}")
        return {"success": False, "error": str(e)}

@app.route('/')
def index():
    """顯示主頁"""
    serial_number = get_serial_number()
    if not serial_number:
        serial_number = "無法獲取序列號"
    return render_template('index.html', machine_id=serial_number)

@app.route('/update', methods=['POST'])
def update():
    """處理更新請求"""
    try:
        name = request.form.get('name', '')
        name_id = request.form.get('name_id', '')
        photo = request.files.get('photo')
        rotation = request.form.get('rotation', '0')
        
        if not all([name, name_id, photo]):
            return jsonify({"success": False, "error": "缺少必要資料"})
            
        result = update_machine_info(name, name_id, photo, rotation)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=6787, debug=True)
