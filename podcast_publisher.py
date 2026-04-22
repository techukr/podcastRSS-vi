import gspread
import requests
import os
import re
import json
from oauth2client.service_account import ServiceAccountCredentials
from github import Github, Auth
from datetime import datetime, timezone

# ================= CẤU HÌNH HỆ THỐNG =================
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = "techukr/podcastRSS"
FILE_PATH = "rss.xml"
BRANCH = "main"

GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1rkvoyKQbquFP21lzCVQhIVF-Ma31chgZqZMy50ba4_I/edit"
WORKSHEET_NAME = "Sheet4"
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
PODCAST_AUTHOR = "ACDT"
# ================= HÀM XỬ LÝ DỮ LIỆU =================


def get_audio_file_size(audio_url):
    try:
        response = requests.head(audio_url, timeout=10, allow_redirects=True)
        if response.status_code in [200, 302]:
            return response.headers.get('Content-Length', '1024000')
    except:
        pass
    return None


def fetch_json_metadata(json_url):
    try:
        response = requests.get(json_url, timeout=10)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None


def replace_or_remove_item(xml_str, guid, new_item_xml=None):
    """
    Tìm vị trí chính xác của 1 tập dựa vào GUID.
    - Nếu có new_item_xml: Thay thế khối XML cũ bằng khối mới (UPDATE).
    - Nếu new_item_xml = None: Xóa hoàn toàn khối XML đó (UNPUBLISH).
    """
    guid_str = f">{guid}</guid>"
    if guid_str not in xml_str:
        return xml_str

    # Tìm điểm neo của GUID
    guid_pos = xml_str.find(guid_str)

    # Dò ngược lên trên tìm thẻ <item> gần nhất
    start_pos = xml_str.rfind("<item>", 0, guid_pos)
    # Lùi lại để lấy các dấu tab/khoảng trắng cho đẹp format
    while start_pos > 0 and xml_str[start_pos-1] in ['\t', ' ']:
        start_pos -= 1

    # Dò xuống dưới tìm thẻ </item> gần nhất
    end_pos = xml_str.find("</item>", guid_pos)

    if start_pos != -1 and end_pos != -1:
        end_pos += len("</item>")
        if end_pos < len(xml_str) and xml_str[end_pos] == '\n':
            end_pos += 1  # Kéo theo cả dấu xuống dòng

        if new_item_xml:
            # UPDATE
            return xml_str[:start_pos] + new_item_xml + "\n" + xml_str[end_pos:]
        else:
            # DELETE
            return xml_str[:start_pos] + xml_str[end_pos:]

    return xml_str

# ================= LOGIC CHÍNH =================


def main():
    print(f"[{datetime.now()}] Bắt đầu quy trình quét và xử lý hàng loạt...")

    if not GOOGLE_CREDENTIALS_JSON:
        print("Lỗi: Không tìm thấy biến môi trường GOOGLE_CREDENTIALS_JSON")
        return

    # 1. KẾT NỐI GOOGLE SHEETS
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(GOOGLE_SHEET_URL).worksheet(WORKSHEET_NAME)
    records = sheet.get_all_records()

    if not records:
        return

    status_col_index = list(records[0].keys()).index("Status") + 1

    # 2. LẤY FILE RSS TỪ GITHUB (CHỈ 1 LẦN)
    auth = Auth.Token(GITHUB_TOKEN)
    g = Github(auth=auth)
    repo = g.get_repo(REPO_NAME)
    file = repo.get_contents(FILE_PATH, ref=BRANCH)
    rss_content = file.decoded_content.decode("utf-8")
    original_rss_content = rss_content

    rows_to_published = []
    rows_to_draft_unlisted = []

    # 3. QUÉT TỪNG DÒNG VÀ SỬA RSS TRÊN RAM
    for index, row in enumerate(records):
        row_number = index + 2
        status = row.get("Status")
        guid = str(row.get("Notebook_ID"))
        topic = row.get("Topic")

        # TRƯỜNG HỢP 1: PUBLISH HOẶC UPDATE TẬP MỚI
        if status == "ready_for_ai":
            audio_url = row.get("Archive_Audio")
            json_url = row.get("Archive_JSON")
            cover_url = row.get("Archive_Cover")

            if not audio_url or not json_url:
                continue

            audio_length = get_audio_file_size(audio_url)
            metadata = fetch_json_metadata(json_url)

            if audio_length and metadata:
                title = metadata.get("title", topic)
                raw_desc = metadata.get(
                    "description", "Nội dung đang cập nhật.")
                description = f"<p>{raw_desc}</p>" if "<p>" not in raw_desc else raw_desc
                duration = metadata.get("duration", "00:15:00")
                pub_date = datetime.now(timezone.utc).strftime(
                    "%a, %d %b %Y %H:%M:%S GMT")

                # Format theo đúng chuẩn Anchor với 2 tab lề
                item_xml = f"""		<item>
            <title><![CDATA[{title}]]></title>
            <description><![CDATA[{description}]]></description>
            <guid isPermaLink="false">{guid}</guid>
            <dc:creator><![CDATA[{PODCAST_AUTHOR}]]></dc:creator>
            <pubDate>{pub_date}</pubDate>
            <enclosure url="{audio_url}" length="{audio_length}" type="audio/mpeg"/>
            <itunes:summary><![CDATA[{description}]]></itunes:summary>
            <itunes:explicit>false</itunes:explicit>
            <itunes:duration>{duration}</itunes:duration>
            <itunes:image href="{cover_url}"/>
            <itunes:episodeType>full</itunes:episodeType>
        </item>"""

                # Logic phân nhánh: Có rồi thì Update, Chưa có thì Insert
                if f">{guid}</guid>" in rss_content:
                    print(f"-> UPDATE: Cập nhật thông tin mới cho '{topic}'")
                    rss_content = replace_or_remove_item(
                        rss_content, guid, item_xml)
                else:
                    print(f"-> INSERT: Thêm mới tập '{topic}' vào RSS")
                    rss_content = rss_content.replace(
                        "</channel>", f"{item_xml}\n\t</channel>")

                rows_to_published.append(row_number)

        # TRƯỜNG HỢP 2: UNPUBLISH BÀI CŨ
        elif status == "draft":
            if f">{guid}</guid>" in rss_content:
                print(f"-> UNPUBLISH: Gỡ bỏ '{topic}' khỏi RSS")
                rss_content = replace_or_remove_item(rss_content, guid, None)

            rows_to_draft_unlisted.append(row_number)

    # 4. ĐẨY FILE RSS ĐÃ SỬA LÊN GITHUB (NẾU CÓ THAY ĐỔI)
    if rss_content != original_rss_content:
        current_time_gmt = datetime.now(
            timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        rss_content = re.sub(r"<lastBuildDate>.*?</lastBuildDate>",
                             f"<lastBuildDate>{current_time_gmt}</lastBuildDate>", rss_content)

        repo.update_file(
            path=FILE_PATH,
            message=f"Batch Update RSS Feed - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            content=rss_content,
            sha=file.sha,
            branch=BRANCH
        )
        print("Đã commit toàn bộ thay đổi lên GitHub thành công!")
    else:
        print("Không có thay đổi nào trên file RSS.")

    # 5. CẬP NHẬT TRẠNG THÁI LÊN GOOGLE SHEETS
    for row_num in rows_to_published:
        sheet.update_cell(row_num, status_col_index, "published")
    for row_num in rows_to_draft_unlisted:
        sheet.update_cell(row_num, status_col_index, "draft_unlisted")

    print("Quy trình hoàn tất!")


if __name__ == "__main__":
    main()
