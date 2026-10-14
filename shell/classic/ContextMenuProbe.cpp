// Acceptance probe for an *installed* RightConvert (used by
// installer/test-setup-install-e2e.ps1). Builds a file's context menu the way
// Explorer does and optionally invokes one entry by its exact caption.
//
//   RightConvertMenuProbe.exe --shell   <file> [caption-to-invoke]
//       Full Shell item menu via IShellFolder::GetUIObjectOf(IContextMenu),
//       so every registered static verb and shellex handler participates.
//   RightConvertMenuProbe.exe --handler <clsid> <file>
//       Only the registered handler class, activated through COM (HKCU
//       registration) and initialized with the Shell's own IDataObject.
//
// Prints one "ITEM: <caption>" line per menu entry and "INVOKED: <caption>"
// after a successful InvokeCommand. Never registers anything itself.
#include <windows.h>
#include <shlobj.h>
#include <shobjidl.h>

#include <cstdio>
#include <string>
#include <vector>

namespace {

struct Entry {
    UINT id;
    std::wstring caption;
};

// Explorer's item-menu flags: CMF_NORMAL | CMF_EXPLORE | CMF_CANRENAME | CMF_ITEMMENU.
constexpr UINT kExplorerFlags = CMF_NORMAL | CMF_EXPLORE | CMF_CANRENAME | CMF_ITEMMENU;
constexpr UINT kFirst = 1;
constexpr UINT kLast = 0x7FFF;

void Collect(HMENU menu, std::vector<Entry>& entries) {
    const int count = GetMenuItemCount(menu);
    for (int index = 0; index < count; ++index) {
        wchar_t text[512]{};
        MENUITEMINFOW info{};
        info.cbSize = sizeof(info);
        info.fMask = MIIM_STRING | MIIM_ID | MIIM_SUBMENU | MIIM_FTYPE;
        info.dwTypeData = text;
        info.cch = static_cast<UINT>(sizeof(text) / sizeof(text[0]) - 1);
        if (!GetMenuItemInfoW(menu, static_cast<UINT>(index), TRUE, &info)) continue;
        if (info.fType & MFT_SEPARATOR) continue;
        std::wstring caption;
        for (const wchar_t* ch = text; *ch; ++ch) {
            if (*ch != L'&') caption += *ch;
        }
        entries.push_back({info.hSubMenu ? 0u : info.wID, caption});
        if (info.hSubMenu) Collect(info.hSubMenu, entries);
    }
}

void PrintUtf8(const char* prefix, const std::wstring& text) {
    const int size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string utf8(size > 0 ? size - 1 : 0, '\0');
    if (size > 1) WideCharToMultiByte(CP_UTF8, 0, text.c_str(), -1, &utf8[0], size, nullptr, nullptr);
    std::printf("%s%s\n", prefix, utf8.c_str());
}

HRESULT UiObject(const wchar_t* path, REFIID iid, void** object) {
    PIDLIST_ABSOLUTE pidl = nullptr;
    HRESULT hr = SHParseDisplayName(path, nullptr, &pidl, 0, nullptr);
    if (FAILED(hr)) return hr;
    IShellFolder* folder = nullptr;
    PCUITEMID_CHILD child = nullptr;
    hr = SHBindToParent(pidl, IID_PPV_ARGS(&folder), &child);
    if (SUCCEEDED(hr)) {
        hr = folder->GetUIObjectOf(nullptr, 1, &child, iid, nullptr, object);
        folder->Release();
    }
    CoTaskMemFree(pidl);
    return hr;
}

int ListAndInvoke(IContextMenu* menu_handler, const wchar_t* invoke) {
    HMENU menu = CreatePopupMenu();
    if (!menu) return 20;
    HRESULT hr = menu_handler->QueryContextMenu(menu, 0, kFirst, kLast, kExplorerFlags);
    if (FAILED(hr)) {
        DestroyMenu(menu);
        std::fprintf(stderr, "QueryContextMenu failed: 0x%08lx\n", static_cast<unsigned long>(hr));
        return 21;
    }
    std::vector<Entry> entries;
    Collect(menu, entries);
    int result = 0;
    bool invoked = false;
    for (const auto& entry : entries) {
        PrintUtf8("ITEM: ", entry.caption);
        if (!invoked && invoke && entry.id >= kFirst && entry.caption == invoke) {
            CMINVOKECOMMANDINFOEX info{};
            info.cbSize = sizeof(info);
            info.fMask = CMIC_MASK_UNICODE | CMIC_MASK_FLAG_NO_UI;
            info.lpVerb = MAKEINTRESOURCEA(entry.id - kFirst);
            info.lpVerbW = MAKEINTRESOURCEW(entry.id - kFirst);
            info.nShow = SW_SHOWNORMAL;
            hr = menu_handler->InvokeCommand(reinterpret_cast<CMINVOKECOMMANDINFO*>(&info));
            if (FAILED(hr)) {
                std::fprintf(stderr, "InvokeCommand failed: 0x%08lx\n", static_cast<unsigned long>(hr));
                result = 22;
            } else {
                PrintUtf8("INVOKED: ", entry.caption);
            }
            invoked = true;
        }
    }
    DestroyMenu(menu);
    if (invoke && !invoked) result = 23;
    return result;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) return 2;
    int result = 1;
    if (argc >= 3 && wcscmp(argv[1], L"--shell") == 0) {
        IContextMenu* menu = nullptr;
        HRESULT hr = UiObject(argv[2], IID_PPV_ARGS(&menu));
        if (FAILED(hr)) {
            std::fprintf(stderr, "GetUIObjectOf(IContextMenu) failed: 0x%08lx\n", static_cast<unsigned long>(hr));
            result = 10;
        } else {
            result = ListAndInvoke(menu, argc >= 4 ? argv[3] : nullptr);
            menu->Release();
        }
    } else if (argc == 4 && wcscmp(argv[1], L"--handler") == 0) {
        CLSID clsid{};
        IContextMenu* menu = nullptr;
        IShellExtInit* init = nullptr;
        IDataObject* data = nullptr;
        HRESULT hr = CLSIDFromString(argv[2], &clsid);
        if (SUCCEEDED(hr)) hr = CoCreateInstance(clsid, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&menu));
        if (SUCCEEDED(hr)) hr = menu->QueryInterface(IID_PPV_ARGS(&init));
        if (SUCCEEDED(hr)) hr = UiObject(argv[3], IID_PPV_ARGS(&data));
        if (SUCCEEDED(hr)) hr = init->Initialize(nullptr, data, nullptr);
        if (FAILED(hr)) {
            std::fprintf(stderr, "Handler activation/initialization failed: 0x%08lx\n", static_cast<unsigned long>(hr));
            result = 11;
        } else {
            result = ListAndInvoke(menu, nullptr);
        }
        if (data) data->Release();
        if (init) init->Release();
        if (menu) menu->Release();
    }
    CoUninitialize();
    return result;
}
