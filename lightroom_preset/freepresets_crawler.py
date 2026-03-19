from bs4 import BeautifulSoup
import time
import os
import requests
import sqlite3
import concurrent.futures
from urllib.parse import unquote


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7"
}

def FreepresetsCrawler():
    page_lower=int(input("Enter the starting page number: "))
    page_upper=int(input("Enter the ending page number: "))
    
    for i in range(page_lower, page_upper + 1):
        response = requests.get(f"https://www.freepresets.com/page/{i}/", headers=HEADERS)
        # print(response.status_code)
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        for a in soup.find_all("a", class_="woocommerce-LoopProduct-link woocommerce-loop-product__link"): 
            if a.has_attr("target") and a.get("target") == "_blank":
                continue   
            raw_text = a.text.strip() 
            raw_text = raw_text.replace("Free Lightroom Preset ", "") # Remove the common prefix from the filename
            filename = raw_text + ".zip" if raw_text else "unknown.zip"
            description_filename = filename.replace(".zip", ".txt") 
            link_to_download_page = a.get("href").strip() if a.has_attr("href") else None
            
            if filename == "unknown.zip" or link_to_download_page is None:
                print("Filename or download page link not found, skipping...")
                continue
            
            response = requests.get(link_to_download_page, headers=HEADERS)
            # print(response.status_code)
            html_inner = response.text
            soup_inner = BeautifulSoup(html_inner, 'html.parser')
            
            for a_inner in soup_inner.find_all("a", class_="wpdm-download-link"):
                link_to_download = a_inner.get("data-downloadurl").strip() if a_inner.has_attr("data-downloadurl") else None
                
                if link_to_download is None:
                    print(f"Download link not found for {filename}")
                    break
                
                file_response = requests.get(link_to_download, headers=HEADERS)
                datasets_zip_save_path = os.path.join("C:/Users/User/Desktop/grad_project_datasets/datasets_zip", filename)
                with open(datasets_zip_save_path, "wb") as f:
                    f.write(file_response.content)
                
                description_file_content = ""
                for li in soup_inner.find_all("li"):
                    for strong in li.find_all("strong"): # Use strong tags to identify important information but get li text instead of strong text
                        description_file_content += li.get_text(strip=True) + "\n"
                description_save_path = os.path.join("C:/Users/User/Desktop/grad_project_datasets/datasets_description", description_filename)
                with open(description_save_path, "w", encoding="utf-8") as f:
                    f.write(description_file_content)
                
                
                print(f"Downloaded: {filename}")
                time.sleep(3)  # Sleep for 3 second to avoid overwhelming the server

if __name__ == "__main__":
    try:
        FreepresetsCrawler()
    except Exception as e:
        print(f"An error occurred: {e}")
    
