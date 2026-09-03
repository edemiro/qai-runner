import xml.etree.ElementTree as ET
import json

def optimize_appium_source(xml_string):
    """
    Appium XML çıktısını AI modelleri için minimize edilmiş bir formata dönüştürür.
    """
    try:
        # XML'i parse et
        root = ET.fromstring(xml_string)
        
        # Kök düğümden (root) temizlemeye başla
        optimized_tree = _parse_node(root)
        
        # Sonucu JSON string olarak döndür (Dilersen dict olarak da bırakabilirsin)
        return json.dumps(optimized_tree, indent=2, ensure_ascii=False)
        
    except ET.ParseError as e:
        return f"XML Parse Hatası: {e}"

def _parse_node(node):
    # 1. KURAL: Ekranda görünmeyen elementleri (ve altındakileri) tamamen yoksay
    if node.attrib.get("displayed") == "false":
        return None

    cleaned_node = {}

    # 2. KURAL: Sınıf isimlerini sadeleştir (android.widget.TextView -> TextView)
    node_class = node.attrib.get("class", node.tag)
    cleaned_node["class"] = node_class.split(".")[-1]

    # 3. KURAL: Sadece AI için hayati olan nitelikleri (attributes) al
    vital_attrs = ["resource-id", "text", "content-desc"]
    
    for attr in vital_attrs:
        val = node.attrib.get(attr)
        # Eğer değer varsa ve boşluktan ibaret değilse ekle
        if val and val.strip():
            # Resource-ID'leri daha da kısalt (com.example.app:id/login_button -> login_button)
            if attr == "resource-id" and ":id/" in val:
                cleaned_node["id"] = val.split(":id/")[-1]
            else:
                cleaned_node[attr] = val

    # 4. KURAL: Alt düğümleri (children) işle
    children = []
    for child in node:
        parsed_child = _parse_node(child)
        if parsed_child:
            children.append(parsed_child)

    # 5. KURAL: Ağacı düzleştir (Flattening) ve boş konteynerleri at
    # Elementin kendine ait önemli bir bilgisi (id, text, desc) var mı?
    has_vital_info = any(key in cleaned_node for key in ["id", "text", "content-desc"])

    if children:
        # Eğer elementin önemli bir bilgisi yoksa ve sadece 1 çocuğu varsa, 
        # bu boş bir kapsayıcıdır (örn. tek çocuklu FrameLayout). Çocuğu direkt yukarı taşı.
        if not has_vital_info and len(children) == 1:
            return children[0]
        
        cleaned_node["children"] = children
    else:
        # Alt elementi yoksa VE önemli bir bilgisi de yoksa bu elementi tamamen çöpe at
        if not has_vital_info:
            return None

    return cleaned_node

# ==========================================
# KULLANIM ÖRNEĞİ
# ==========================================

raw_appium_xml = """
<hierarchy rotation="0">
    <android.widget.FrameLayout bounds="[0,0][1080,2400]" displayed="true">
        <android.widget.LinearLayout bounds="[0,0][1080,2400]" displayed="true" focusable="false">
            <android.widget.TextView class="android.widget.TextView" text="Hoş Geldiniz" resource-id="com.app:id/titleText" displayed="true" bounds="[100,200][900,300]"/>
            <android.widget.Button class="android.widget.Button" text="Giriş Yap" resource-id="com.app:id/loginBtn" displayed="true" bounds="[100,500][900,600]"/>
            <android.widget.TextView class="android.widget.TextView" text="Gizli Hata" displayed="false" bounds="[0,0][0,0]"/>
        </android.widget.LinearLayout>
    </android.widget.FrameLayout>
</hierarchy>
"""

optimized_json = optimize_appium_source(raw_appium_xml)
print(optimized_json)