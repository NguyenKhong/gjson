import json
import re
from json.decoder import scanstring, WHITESPACE
from json import detect_encoding, JSONDecodeError
from time import time
# Regex để nhận diện số (Number) theo chuẩn JSON
NUMBER_RE = re.compile(
    r'(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?',
    (re.VERBOSE | re.MULTILINE | re.DOTALL))

TYPE_OBJ = 0
TYPE_ARR = 1

class IterativeJSONParser:
    def parse(self, s, encoding="utf8"):
        # Cache các hàm global vào local để truy cập nhanh hơn trong vòng lặp
        _ws_match = WHITESPACE.match
        _scanstring = scanstring
        _number_match = NUMBER_RE.match
        if isinstance(s, str):
            if s.startswith('\ufeff'):
                raise JSONDecodeError("Unexpected UTF-8 BOM (decode using utf-8-sig)", s, 0)
        elif hasattr(s, "read"):
            s = s.read().decode(encoding)
        else:
            if isinstance(s, (bytes, bytearray)):
                s = s.decode(detect_encoding(s), 'surrogatepass')
            else:
                raise TypeError(f'the JSON object must be str, bytes or bytearray, not {s.__class__.__name__}')
        length = len(s)
        
        # Tìm điểm bắt đầu
        idx = _ws_match(s, 0).end()
        if idx >= length:
            return

        # Stack lưu tuple: (TYPE, first_element_flag)
        # first_element_flag = True nghĩa là chưa parse phần tử nào (để xử lý dấu phẩy)
        stack = []
        
        # Khởi tạo stack dựa trên ký tự đầu
        char = s[idx]
        if char == '{':
            yield ('start_map', None)
            stack.append([TYPE_OBJ, True]) # Dùng list thay vì tuple để có thể sửa đổi flag
            idx += 1
        elif char == '[':
            yield ('start_array', None)
            stack.append([TYPE_ARR, True])
            idx += 1
        else:
            raise JSONDecodeError("JSON phải bắt đầu bằng { hoặc [", s, idx)

        # Vòng lặp chính (Thay thế cho đệ quy)
        while stack:
            # Lấy ngữ cảnh hiện tại (không pop ngay)
            context = stack[-1]
            container_type = context[0]
            
            idx = _ws_match(s, idx).end()
            if idx >= length:
                raise JSONDecodeError("Unexpected EOF", s, idx)

            char = s[idx]

            # --- XỬ LÝ DẤU ĐÓNG CONTAINER ---
            if container_type == TYPE_OBJ and char == '}':
                stack.pop()
                yield ('end_map', None)
                idx += 1
                # Kiểm tra xem có cần xử lý dấu phẩy sau khi đóng không (cho phần tử cha)
                continue
            elif container_type == TYPE_ARR and char == ']':
                stack.pop()
                yield ('end_array', None)
                idx += 1
                continue

            # --- XỬ LÝ DẤU PHẨY (COMMA) ---
            # Nếu không phải phần tử đầu tiên, bắt buộc phải có dấu phẩy
            if not context[1]: 
                if char == ',':
                    idx += 1
                    idx = _ws_match(s, idx).end()
                    char = s[idx]
                    # Xử lý TRAILING COMMA: {"a":1, } hoặc [1, ]
                    if (container_type == TYPE_OBJ and char == '}') or \
                       (container_type == TYPE_ARR and char == ']'):
                        continue # Quay lại đầu vòng lặp để block xử lý dấu đóng bắt lấy
                else:
                    # Nếu không có dấu phẩy, mà cũng không phải dấu đóng -> Lỗi
                    # (Hoặc bạn có thể bỏ qua dòng này nếu muốn support JSON thiếu dấu phẩy)
                    raise JSONDecodeError("Expecting ','", s, idx)
            else:
                # Đã qua phần tử đầu tiên, set flag = False
                context[1] = False

            # --- XỬ LÝ KEY (NẾU LÀ OBJECT) ---
            if container_type == TYPE_OBJ:
                if char != '"':
                    raise JSONDecodeError("Expecting property name", s, idx)
                
                key, idx = _scanstring(s, idx + 1)
                yield ('map_key', key)

                idx = _ws_match(s, idx).end()
                if s[idx] != ':':
                    raise JSONDecodeError("Expecting ':'", s, idx)
                idx += 1
                idx = _ws_match(s, idx).end()
                char = s[idx]

            # --- XỬ LÝ VALUE (CHO CẢ OBJECT VÀ ARRAY) ---
            # Logic xác định value giống nhau cho cả 2
            
            if char == '"':
                val, idx = _scanstring(s, idx + 1)
                yield ('value', val)
            
            elif char == '{':
                yield ('start_map', None)
                stack.append([TYPE_OBJ, True]) # Push context mới
                idx += 1
            
            elif char == '[':
                yield ('start_array', None)
                stack.append([TYPE_ARR, True]) # Push context mới
                idx += 1
            
            elif char == 't' and s.startswith('true', idx):
                yield ('value', True)
                idx += 4
            
            elif char == 'f' and s.startswith('false', idx):
                yield ('value', False)
                idx += 5
            
            elif char == 'n' and s.startswith('null', idx):
                yield ('value', None)
                idx += 4
            
            else:
                # Xử lý số
                m = _number_match(s, idx)
                if m:
                    num_str = m.group(0)
                    if '.' in num_str or 'e' in num_str or 'E' in num_str:
                        yield ('value', float(num_str))
                    else:
                        yield ('value', int(num_str))
                    idx = m.end()
                else:
                    raise JSONDecodeError(f"Unexpected character '{char}'", s, idx)

class IterativeBufferedJSONParser:
    def __init__(self, chunk_size=64*1024, encoding='utf-8'):
        self.chunk_size = chunk_size # 64KB mặc định
        self.encoding = encoding
        
        # Buffer quản lý
        self.buf = ""
        self.idx = 0
        self.file_handle = None
        self.eof = False
        self._ws_match = WHITESPACE.match

    def _ensure_buffer(self, min_needed=1):
        """
        Đảm bảo buffer còn đủ dữ liệu để đọc.
        Nếu sắp hết, thực hiện nối phần dư với chunk mới đọc từ file.
        """
        # Nếu còn nhiều dữ liệu trong buffer, không làm gì cả
        # print("_ensure_buffer")
        if self.idx + min_needed < len(self.buf):
            return True

        if self.eof:
            return False
        buff_temp = []
        # Lấy phần dư chưa xử lý (Tail)
        buff_temp.append(self.buf[self.idx:])
        
        # Đọc thêm chunk mới
        new_data = self.file_handle.read(self.chunk_size)
        if not new_data:
            self.eof = True
            self.buf = "".join(buff_temp) # Giữ lại phần dư cuối cùng
            self.idx = 0
            return len(self.buf) > 0 # Trả về False nếu thực sự hết sạch
        buff_temp.append(new_data)
        # Nối chuỗi (Đây là đoạn tốn chi phí nhất của phương pháp này)
        self.buf = "".join(buff_temp)
        self.idx = 0
        match = self._ws_match(self.buf, self.idx)
        if match:
            self.buf = self.buf[match.end():]
        return True

    def parse(self, file):
        # Cache local functions
        _ws_match = WHITESPACE.match
        _scanstring = scanstring
        _number_match = NUMBER_RE.match

        with open(file, "r", encoding=self.encoding) as f:
            self.file_handle = f
            self.buf = f.read(self.chunk_size)
            self.idx = 0
            self.eof = False
            
            # Bỏ qua khoảng trắng đầu file
            while True:
                match = _ws_match(self.buf, self.idx)
                if match:
                    self.idx = match.end()
                
                # Nếu idx chạm đáy buffer, load thêm
                if self.idx >= len(self.buf):
                    if not self._ensure_buffer():
                        return # EOF
                else:
                    break

            # Khởi tạo Stack
            stack = []
            char = self.buf[self.idx]
            
            if char == '{':
                yield ('start_map', None)
                stack.append([TYPE_OBJ, True])
                self.idx += 1
            elif char == '[':
                yield ('start_array', None)
                stack.append([TYPE_ARR, True])
                self.idx += 1
            else:
                raise JSONDecodeError("Start with { or [", self.buf, self.idx)

            # --- VÒNG LẶP CHÍNH ---
            while stack:
                # 1. Luôn đảm bảo buffer có ít nhất vài ký tự để check
                if self.idx >= len(self.buf):
                    if not self._ensure_buffer():
                        raise JSONDecodeError("Unexpected EOF", self.buf, self.idx)

                context = stack[-1]
                container_type = context[0]

                # Skip whitespace
                match = _ws_match(self.buf, self.idx)
                if match:
                    self.idx = match.end()

                # Kiểm tra lại buffer sau khi skip whitespace
                if self.idx >= len(self.buf):
                     if not self._ensure_buffer():
                        raise JSONDecodeError("Unexpected EOF", self.buf, self.idx)
                # print(self.buf, self.buf[self.idx])
                char = self.buf[self.idx]

                # --- XỬ LÝ DẤU ĐÓNG ---
                if container_type == TYPE_OBJ and char == '}':
                    stack.pop()
                    yield ('end_map', None)
                    self.idx += 1
                    continue
                elif container_type == TYPE_ARR and char == ']':
                    stack.pop()
                    yield ('end_array', None)
                    self.idx += 1
                    continue

                # --- XỬ LÝ DẤU PHẨY ---
                if not context[1]:
                    if char == ',':
                        self.idx += 1
                        # Skip whitespace sau dấu phẩy
                        match = _ws_match(self.buf, self.idx)
                        if match: 
                            self.idx = match.end()
                        
                        # Reload buffer nếu cần để check ký tự kế tiếp (Trailing comma)
                        if self.idx >= len(self.buf):
                            if not self._ensure_buffer(): 
                                raise JSONDecodeError("Unexpected EOF", "", 0)

                        next_char = self.buf[self.idx]
                        if (container_type == TYPE_OBJ and next_char == '}') or \
                           (container_type == TYPE_ARR and next_char == ']'):
                            continue
                    else:
                        # print(self.buf, self.buf[self.idx])
                        # Logic lỏng lẻo: Nếu thiếu dấu phẩy nhưng gặp ký tự khác, có thể raise lỗi hoặc bỏ qua
                        raise JSONDecodeError("Expecting ','", self.buf, self.idx)
                else:
                    context[1] = False

                # --- XỬ LÝ KEY (OBJECT) ---
                if container_type == TYPE_OBJ:
                    if self.idx >= len(self.buf):
                        if not self._ensure_buffer(): 
                            raise JSONDecodeError("Unexpected EOF expecting Key", "", 0)

                    if self.buf[self.idx] != '"':
                        # print(self.buf)
                        raise JSONDecodeError("Expecting property name", self.buf, self.idx)
                    
                    # SCANSTRING VỚI RETRY
                    # scanstring có thể fail nếu chuỗi bị cắt giữa chừng (buffer hết)
                    try:
                        key, self.idx = _scanstring(self.buf, self.idx + 1)
                    except JSONDecodeError:
                        # Nếu lỗi, khả năng cao là hết buffer giữa chuỗi -> Load thêm và thử lại
                        if self._ensure_buffer(64*1024): # Load thêm chunk
                            key, self.idx = _scanstring(self.buf, self.idx + 1)
                        else:
                            raise

                    yield ('map_key', key)

                    # Skip :
                    match = _ws_match(self.buf, self.idx)
                    if match: self.idx = match.end()
                    
                    if self.idx >= len(self.buf): 
                        self._ensure_buffer()
                    
                    if self.buf[self.idx] != ':':
                        raise JSONDecodeError("Expecting :", self.buf, self.idx)
                    self.idx += 1
                    
                    match = _ws_match(self.buf, self.idx)
                    if match: 
                        self.idx = match.end()
                    if self.idx >= len(self.buf): 
                        self._ensure_buffer()

                # --- XỬ LÝ VALUE ---
                char = self.buf[self.idx]

                if char == '"':
                    try:
                        val, self.idx = _scanstring(self.buf, self.idx + 1)
                    except JSONDecodeError:
                        self._ensure_buffer(64*1024)
                        val, self.idx = _scanstring(self.buf, self.idx + 1)
                    yield ('value', val)

                elif char == '{':
                    yield ('start_map', None)
                    stack.append([TYPE_OBJ, True])
                    self.idx += 1

                elif char == '[':
                    yield ('start_array', None)
                    stack.append([TYPE_ARR, True])
                    self.idx += 1

                elif char == 't':
                    # Cần đảm bảo đủ ký tự để check 'true'
                    if self.idx + 4 > len(self.buf): 
                        self._ensure_buffer(4)
                    if self.buf.startswith('true', self.idx):
                        yield ('value', True)
                        self.idx += 4

                elif char == 'f':
                    if self.idx + 5 > len(self.buf): 
                        self._ensure_buffer(5)
                    if self.buf.startswith('false', self.idx):
                        yield ('value', False)
                        self.idx += 5
                
                elif char == 'n':
                    if self.idx + 4 > len(self.buf): 
                        self._ensure_buffer(4)
                    if self.buf.startswith('null', self.idx):
                        yield ('value', None)
                        self.idx += 4

                else:
                    # Xử lý số (Number)
                    # Số có thể bị cắt đôi (vd: 123|456). Regex sẽ không match hết.
                    # Chiến thuật: Thử match, nếu match chạm đáy buffer -> load thêm -> match lại
                    
                    m = _number_match(self.buf, self.idx)
                    if m:
                        # Nếu match kết thúc đúng tại cuối buffer, có nguy cơ số chưa hết
                        if m.end() == len(self.buf) and not self.eof:
                            self._ensure_buffer()
                            m = _number_match(self.buf, self.idx) # Match lại trên buffer mới nối
                        
                        num_str = m.group(0)
                        if '.' in num_str or 'e' in num_str or 'E' in num_str:
                            yield ('value', float(num_str))
                        else:
                            yield ('value', int(num_str))
                        self.idx = m.end()
                    else:
                        raise JSONDecodeError(f"Unexpected char '{char}'", self.buf, self.idx)

def parse_base(parser_generator):
    path = []
    for event, value in parser_generator:
        if event == 'map_key':
            prefix = '.'.join(path[:-1])
            path[-1] = value
        elif event == 'start_map':
            prefix = '.'.join(path)
            path.append(None)
        elif event == 'end_map':
            path.pop()
            prefix = '.'.join(path)
        elif event == 'start_array':
            prefix = '.'.join(path)
            path.append('item')
        elif event == 'end_array':
            path.pop()
            prefix = '.'.join(path)
        else: # any scalar value
            prefix = '.'.join(path)
        yield (prefix, event, value)

def events_to_object(parser_generator):
    """
    Hàm gom các sự kiện từ parser thành một Python Dict hoặc List hoàn chỉnh.
    """
    root = None
    stack = [] # Stack chứa (container, key_nếu_có)

    for event_type, value in parser_generator:
        
        # 1. Bắt đầu một Object hoặc Array mới
        if event_type in ('start_map', 'start_array'):
            new_container = {} if event_type == 'start_map' else []
            
            if not stack:
                root = new_container
                stack.append((root, None)) # Root không có key
            else:
                parent, current_key = stack[-1]
                if isinstance(parent, list):
                    parent.append(new_container)
                else:
                    parent[current_key] = new_container
                
                stack.append((new_container, None))

        # 2. Key của Map
        elif event_type == 'map_key':
            # Cập nhật key đang chờ xử lý ở đỉnh stack
            container, _ = stack.pop()
            stack.append((container, value))

        # 3. Kết thúc Object hoặc Array
        elif event_type in ('end_map', 'end_array'):
            stack.pop()

        # 4. Giá trị (String, Int, Bool, Null...)
        elif event_type == 'value':
            parent, current_key = stack[-1]
            if isinstance(parent, list):
                parent.append(value)
            else:
                parent[current_key] = value

    return root

class FastJSONParser:
    """
    Parse json using stack instead of recursion
    Support json type like as: {"a": 1,} or [1,] or {"a": 1}more data
    """
    def parse(self, s, encoding="utf8"):
        """
        Parse chuỗi JSON (hoặc mmap string) thành Python Object.
        - Khử đệ quy (Dùng Stack).
        - Hỗ trợ dấu phẩy thừa (Trailing commas).
        - Bỏ qua dữ liệu rác sau khi kết thúc Root Object.
        """
        # --- LOCAL VARIABLE CACHING (Tăng tốc độ truy cập trong loop) ---
        _ws_match = WHITESPACE.match
        _scanstring = scanstring
        _number_match = NUMBER_RE.match

        if isinstance(s, str):
            if s.startswith('\ufeff'):
                raise JSONDecodeError("Unexpected UTF-8 BOM (decode using utf-8-sig)", s, 0)
        elif hasattr(s, "read"):
            s = s.read().decode(encoding)
        else:
            if isinstance(s, (bytes, bytearray)):
                s = s.decode(detect_encoding(s), 'surrogatepass')
            else:
                raise TypeError(f'the JSON object must be str, bytes or bytearray, not {s.__class__.__name__}')

        # Tìm điểm bắt đầu
        length = len(s)
        idx = _ws_match(s, 0).end()
        
        if idx >= length:
            raise ValueError("Empty string")

        # Xác định Root container
        root = None
        char = s[idx]
        
        # Stack lưu trữ tuple: (container_object, is_dict_boolean)
        # container_object: là list hoặc dict đang được xây dựng
        # is_dict_boolean: True nếu là dict, False nếu là list (để tránh gọi isinstance nhiều lần)
        stack = [] 
        
        if char == '{':
            root = {}
            stack.append((root, True))
            idx += 1
        elif char == '[':
            root = []
            stack.append((root, False))
            idx += 1
        else:
            raise JSONDecodeError("JSON must start with { or [", s, idx)

        # --- VÒNG LẶP CHÍNH (Iterative) ---
        while stack:
            # Lấy container hiện tại ở đỉnh stack (không pop)
            current_container, is_dict = stack[-1]
            
            # Bỏ qua khoảng trắng
            idx = _ws_match(s, idx).end()
            if idx >= length:
                raise JSONDecodeError("Unexpected EOF", s, idx)
            
            char = s[idx]

            # 1. KIỂM TRA ĐÓNG CONTAINER (} hoặc ])
            if char == '}':
                if is_dict:
                    stack.pop()
                    idx += 1
                    continue
                else:
                    raise JSONDecodeError("Expecting }", s, idx)
            elif char == ']':
                if not is_dict:
                    stack.pop()
                    idx += 1
                    continue
                else:
                    raise JSONDecodeError("Expecting ]", s, idx)

            # 2. XỬ LÝ DẤU PHẨY (COMMA)
            # Nếu container đã có dữ liệu, bắt buộc phải có dấu phẩy hoặc là dấu đóng (đã check ở trên)
            # len(current_container) > 0 kiểm tra nhanh hơn là dùng cờ flag
            if len(current_container) > 0:
                if char == ',':
                    idx += 1
                    idx = _ws_match(s, idx).end()
                    char = s[idx]
                    
                    # --- XỬ LÝ TRAILING COMMA (Quan trọng) ---
                    # Nếu sau dấu phẩy là dấu đóng } hoặc ], quay lại đầu vòng lặp để mục 1 xử lý
                    if (is_dict and char == '}') or (not is_dict and char == ']'):
                        continue
                else:
                    # Nếu không có dấu phẩy giữa các phần tử -> Lỗi
                    raise JSONDecodeError("Expecting ',' delimiter", s, idx)

            # 3. PARSE KEY (Chỉ cho Object)
            key = None
            if is_dict:
                if char != '"':
                    raise JSONDecodeError("Expecting property name enclosed in double quotes", s, idx)
                
                # scanstring trả về (chuỗi, vị trí kết thúc)
                key, idx = _scanstring(s, idx + 1)
                
                idx = _ws_match(s, idx).end()
                if s[idx] != ':':
                    raise JSONDecodeError("Expecting ':' delimiter", s, idx)
                idx += 1
                idx = _ws_match(s, idx).end()
                char = s[idx]

            # 4. PARSE VALUE (Cho cả Object và Array)
            # Biến lưu giá trị parse được
            val = None
            # Cờ đánh dấu xem value có phải là container mới (nested) hay không
            is_new_container = False
            new_is_dict = False

            if char == '"':
                val, idx = _scanstring(s, idx + 1)
            
            elif char == '{':
                val = {}
                is_new_container = True
                new_is_dict = True
                idx += 1
            
            elif char == '[':
                val = []
                is_new_container = True
                new_is_dict = False
                idx += 1
            
            elif char == 't' and s.startswith('true', idx):
                val = True
                idx += 4
            
            elif char == 'f' and s.startswith('false', idx):
                val = False
                idx += 5
            
            elif char == 'n' and s.startswith('null', idx):
                val = None
                idx += 4
                
            else:
                # Parse Number
                m = _number_match(s, idx)
                if m:
                    num_str = m.group(0)
                    if '.' in num_str or 'e' in num_str or 'E' in num_str:
                        val = float(num_str)
                    else:
                        val = int(num_str)
                    idx = m.end()
                else:
                    raise JSONDecodeError(f"Unexpected character '{char}'", s, idx)

            # 5. GÁN VALUE VÀO CONTAINER HIỆN TẠI
            if is_dict:
                current_container[key] = val
            else:
                current_container.append(val)

            # 6. NẾU VALUE LÀ CONTAINER MỚI -> ĐẨY VÀO STACK
            if is_new_container:
                stack.append((val, new_is_dict))

        # Kết thúc vòng lặp (Stack rỗng) -> Trả về root
        # Bất kỳ dữ liệu thừa nào sau idx hiện tại đều bị bỏ qua (đúng ý bạn)
        return root

def main():
    print(events_to_object(IterativeBufferedJSONParser().parse(r"test.json")))
    print(events_to_object(IterativeJSONParser().parse('{"a": 1}')))
    return
    t = time()
    for i in parse_base(IterativeBufferedJSONParser().parse(r"test.json")):
        print(i)
    print(time()-t)

if __name__ == '__main__':
    main()
