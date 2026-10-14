// HKCU classic Explorer verb via COM DelegateExecute + MultiSelectModel=Player.
// Separate CLSID from the Windows 11 sparse-MSIX modern menu.
// Explorer supplies the complete selection via IObjectWithSelection::SetSelection.
#include <windows.h>
#include <shobjidl.h>
#include <shlobj.h>
#include <shellapi.h>
#include <algorithm>
#include <cwctype>

#include <atomic>
#include <new>
#include <string>
#include <vector>

namespace {
constexpr CLSID kClassicCommandClsid = {
    0xd172f75d, 0x5505, 0x42a8, {0xae, 0x5a, 0x38, 0x0b, 0x04, 0x06, 0xb4, 0x8c}
};
// Native IContextMenu handler; no registry-cascade/CommandStore dependency.
constexpr CLSID kQuickCascadeClsid = {
    0x839e65d5, 0xa748, 0x4a0f, {0xa9, 0xf3, 0xa8, 0x46, 0x92, 0x4a, 0x75, 0x35}
};
// Fixed, separate COM classes keep each classic cascade leaf's target immutable.
// Keep these IDs synchronized with installer/common.ps1.
struct QuickTarget {
    CLSID clsid;
    const wchar_t* format;
};
constexpr QuickTarget kQuickTargets[] = {
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x01}}, L"jpg"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x02}}, L"png"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x03}}, L"webp"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x04}}, L"mp3"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x05}}, L"wav"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x06}}, L"flac"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x07}}, L"mp4"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x08}}, L"webm"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x09}}, L"pdf"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x10}}, L"tiff"},
    {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x11}}, L"pngzip"},
};
std::atomic<long> live_objects{0};
HMODULE module_handle = nullptr;

std::wstring QuoteArgument(const std::wstring& argument) {
    // Quoting for Windows CRT argv: escape a quote plus its preceding slashes.
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t ch : argument) {
        if (ch == L'\\') {
            ++slashes;
        } else {
            result.append(slashes * (ch == L'"' ? 2 : 1), L'\\');
            if (ch == L'"') result += L'\\';
            result += ch;
            slashes = 0;
        }
    }
    result.append(slashes * 2, L'\\');
    return result + L"\"";
}

std::wstring ExecutablePath() {
    if (!module_handle) return {};
    std::vector<wchar_t> buffer(MAX_PATH);
    for (;;) {
        const DWORD size = GetModuleFileNameW(module_handle, buffer.data(),
                                              static_cast<DWORD>(buffer.size()));
        if (size == 0) return {};
        if (size < buffer.size()) {
            std::wstring dll_path(buffer.data(), size);
            const auto slash = dll_path.find_last_of(L"\\/");
            return slash == std::wstring::npos ? std::wstring() :
                   dll_path.substr(0, slash + 1) + L"RightConvert.exe";
        }
        if (buffer.size() >= 32768) return {};
        buffer.resize(buffer.size() * 2);
    }
}

class ClassicCommand final : public IExecuteCommand, public IObjectWithSelection {
public:
    explicit ClassicCommand(const wchar_t* target) : target_(target) { ++live_objects; }
    ~ClassicCommand() { if (selection_) selection_->Release(); --live_objects; }

    IFACEMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_IExecuteCommand) {
            *object = static_cast<IExecuteCommand*>(this);
        } else if (iid == IID_IObjectWithSelection) {
            *object = static_cast<IObjectWithSelection*>(this);
        } else {
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = static_cast<ULONG>(InterlockedDecrement(&references_));
        if (!remaining) delete this;
        return remaining;
    }

    IFACEMETHODIMP SetSelection(IShellItemArray* items) override {
        if (!items) return E_INVALIDARG;
        items->AddRef();
        if (selection_) selection_->Release();
        selection_ = items;
        return S_OK;
    }
    IFACEMETHODIMP GetSelection(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (!selection_) return E_FAIL;
        return selection_->QueryInterface(iid, object);
    }

    IFACEMETHODIMP SetKeyState(DWORD) override { return S_OK; }
    IFACEMETHODIMP SetParameters(LPCWSTR) override { return S_OK; }
    IFACEMETHODIMP SetPosition(POINT) override { return S_OK; }
    IFACEMETHODIMP SetShowWindow(int) override { return S_OK; }
    IFACEMETHODIMP SetNoShowUI(BOOL) override { return S_OK; }
    IFACEMETHODIMP SetDirectory(LPCWSTR) override { return S_OK; }

    IFACEMETHODIMP Execute() override {
        if (!selection_) return E_INVALIDARG;
        const std::wstring executable = ExecutablePath();
        if (executable.empty()) return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
        DWORD count = 0;
        HRESULT hr = selection_->GetCount(&count);
        if (FAILED(hr)) return hr;
        if (!count) return E_INVALIDARG;

        std::wstring command = QuoteArgument(executable) + L" --files";
        // Gather all paths before creating the process. On any invalid item or
        // command line overflow, fail instead of launching a partial selection.
        for (DWORD index = 0; index < count; ++index) {
            IShellItem* item = nullptr;
            hr = selection_->GetItemAt(index, &item);
            if (FAILED(hr)) return hr;
            PWSTR path = nullptr;
            hr = item->GetDisplayName(SIGDN_FILESYSPATH, &path);
            item->Release();
            if (FAILED(hr)) return hr;
            if (!path) return E_FAIL;
            command += L" " + QuoteArgument(path);
            CoTaskMemFree(path);
            if (command.size() >= 32767) {
                return HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE);
            }
        }

        if (target_) {
            // Do not convert inside Explorer. The fixed target is validated by
            // the separate app process, preserving source and output semantics.
            command += L" --target ";
            command += target_;
            command += L" --direct --notify";
            if (command.size() >= 32767) {
                return HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE);
            }
        }

        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION process{};
        if (!CreateProcessW(executable.c_str(), &command[0], nullptr, nullptr,
                            FALSE, 0, nullptr, nullptr, &startup, &process)) {
            return HRESULT_FROM_WIN32(GetLastError());
        }
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return S_OK;
    }
private:
    volatile LONG references_{1};
    IShellItemArray* selection_{nullptr};
    const wchar_t* const target_;
};

// Windows 10 and Windows 11 classic Explorer: supply a real HMENU submenu
// instead of registry ExtendedSubCommandsKey / CommandStore placeholders.
// This runs in Explorer only to collect file paths and draw a menu; the actual
// conversion always runs as a separate RightConvert.exe --direct process.
class ContextQuickMenu final : public IContextMenu, public IShellExtInit {
public:
    ContextQuickMenu() { ++live_objects; }
    ~ContextQuickMenu() { --live_objects; }
    IFACEMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_IContextMenu) {
            *object = static_cast<IContextMenu*>(this);
        } else if (iid == IID_IShellExtInit) {
            *object = static_cast<IShellExtInit*>(this);
        } else {
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = static_cast<ULONG>(InterlockedDecrement(&references_));
        if (!remaining) delete this;
        return remaining;
    }
    IFACEMETHODIMP Initialize(PCIDLIST_ABSOLUTE, IDataObject* data, HKEY) override {
        sources_.clear();
        offsets_.clear();
        if (!data) return E_INVALIDARG;
        FORMATETC format{CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
        STGMEDIUM medium{};
        HRESULT hr = data->GetData(&format, &medium);
        if (FAILED(hr)) return hr;
        HDROP drop = static_cast<HDROP>(GlobalLock(medium.hGlobal));
        if (!drop) {
            ReleaseStgMedium(&medium);
            return E_FAIL;
        }
        const UINT count = DragQueryFileW(drop, 0xFFFFFFFF, nullptr, 0);
        if (!count || count > 1000) {
            GlobalUnlock(medium.hGlobal);
            ReleaseStgMedium(&medium);
            return E_INVALIDARG;
        }
        for (UINT i = 0; i < count; ++i) {
            const UINT size = DragQueryFileW(drop, i, nullptr, 0);
            if (!size || size > 32760) { sources_.clear(); break; }
            std::wstring path(size + 1, L'\0');
            if (DragQueryFileW(drop, i, &path[0], size + 1) != size) {
                sources_.clear(); break;
            }
            path.resize(size);
            const DWORD attributes = GetFileAttributesW(path.c_str());
            if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
                sources_.clear(); break;
            }
            sources_.push_back(std::move(path));
        }
        GlobalUnlock(medium.hGlobal);
        ReleaseStgMedium(&medium);
        return sources_.size() == count ? S_OK : E_INVALIDARG;
    }
    IFACEMETHODIMP QueryContextMenu(HMENU menu, UINT index, UINT idFirst,
                                    UINT idLast, UINT flags) override {
        offsets_.clear();
        if (!menu || (flags & CMF_DEFAULTONLY) || sources_.empty()) return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
        for (size_t i = 0; i < sizeof(kQuickTargets) / sizeof(kQuickTargets[0]); ++i) {
            const wchar_t* target = kQuickTargets[i].format;
            if (!AcceptsTarget(target)) continue;
            if (offsets_.size() >= static_cast<size_t>(idLast - idFirst + 1)) break;
            const wchar_t* label = wcscmp(target, L"pngzip") == 0 ?
                L"PNG (all pages, ZIP)" : target;
            std::wstring caption = L"Convert to ";
            caption += label;
            MENUITEMINFOW option{};
            option.cbSize = sizeof(option);
            option.fMask = MIIM_STRING | MIIM_ID;
            option.dwTypeData = &caption[0];
            option.wID = idFirst + static_cast<UINT>(offsets_.size());
            if (!InsertMenuItemW(menu, index + static_cast<UINT>(offsets_.size()), TRUE, &option))
                return HRESULT_FROM_WIN32(GetLastError());
            offsets_.push_back(i);
        }
        return MAKE_HRESULT(SEVERITY_SUCCESS, 0, static_cast<USHORT>(offsets_.size()));
    }
    IFACEMETHODIMP InvokeCommand(LPCMINVOKECOMMANDINFO command) override {
        if (!command || HIWORD(command->lpVerb)) return E_INVALIDARG;
        const size_t offset = LOWORD(command->lpVerb);
        if (offset >= offsets_.size() || sources_.empty()) return E_INVALIDARG;
        const std::wstring exe = ExecutablePath();
        if (exe.empty()) return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
        std::wstring arguments = QuoteArgument(exe) + L" --files";
        for (const auto& path : sources_) {
            arguments += L" " + QuoteArgument(path);
            if (arguments.size() >= 32000) return HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE);
        }
        arguments += L" --target ";
        arguments += kQuickTargets[offsets_[offset]].format;
        arguments += L" --direct --notify";
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION process{};
        if (!CreateProcessW(exe.c_str(), &arguments[0], nullptr, nullptr,
                            FALSE, 0, nullptr, nullptr, &startup, &process)) {
            return HRESULT_FROM_WIN32(GetLastError());
        }
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return S_OK;
    }
    IFACEMETHODIMP GetCommandString(UINT_PTR, UINT, UINT*, LPSTR, UINT) override {
        return E_NOTIMPL;
    }
private:
    bool AcceptsTarget(const wchar_t* target) const {
        for (const auto& path : sources_) {
            const auto pos = path.find_last_of(L'.');
            if (pos == std::wstring::npos) return false;
            std::wstring ext = path.substr(pos + 1);
            std::transform(ext.begin(), ext.end(), ext.begin(), towlower);
            const bool image = ext == L"jpg" || ext == L"jpeg" || ext == L"png" ||
                               ext == L"webp" || ext == L"bmp" || ext == L"tiff" ||
                               ext == L"tif" || ext == L"gif";
            const bool audio = ext == L"mp3" || ext == L"wav" || ext == L"flac" ||
                               ext == L"ogg" || ext == L"opus";
            const bool video = ext == L"mp4" || ext == L"webm" || ext == L"mov" ||
                               ext == L"avi" || ext == L"mkv";
            const bool office = ext == L"docx" || ext == L"xlsx" || ext == L"pptx" ||
                                ext == L"odt" || ext == L"ods" || ext == L"odp";
            const bool ok = (image && (wcscmp(target, L"jpg") == 0 ||
                                         wcscmp(target, L"png") == 0 ||
                                         wcscmp(target, L"webp") == 0 ||
                                         wcscmp(target, L"tiff") == 0 ||
                                         wcscmp(target, L"pdf") == 0)) ||
                            (ext == L"pdf" && (wcscmp(target, L"tiff") == 0 ||
                                                wcscmp(target, L"pngzip") == 0)) ||
                            (audio && (wcscmp(target, L"mp3") == 0 ||
                                       wcscmp(target, L"wav") == 0 ||
                                       wcscmp(target, L"flac") == 0)) ||
                            (video && (wcscmp(target, L"mp4") == 0 ||
                                       wcscmp(target, L"webm") == 0)) ||
                            (office && wcscmp(target, L"pdf") == 0);
            if (!ok || ext == target) return false;
        }
        return true;
    }
    volatile LONG references_{1};
    std::vector<std::wstring> sources_;
    std::vector<size_t> offsets_;
};

class CommandFactory final : public IClassFactory {
public:
    explicit CommandFactory(const wchar_t* target, bool cascade = false)
        : target_(target), cascade_(cascade) { ++live_objects; }
    ~CommandFactory() { --live_objects; }
    IFACEMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid != IID_IUnknown && iid != IID_IClassFactory) return E_NOINTERFACE;
        *object = static_cast<IClassFactory*>(this);
        AddRef();
        return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = static_cast<ULONG>(InterlockedDecrement(&references_));
        if (!remaining) delete this;
        return remaining;
    }
    IFACEMETHODIMP CreateInstance(IUnknown* aggregate, REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (aggregate) return CLASS_E_NOAGGREGATION;
        if (cascade_) {
            ContextQuickMenu* instance = new (std::nothrow) ContextQuickMenu();
            if (!instance) return E_OUTOFMEMORY;
            const HRESULT hr = instance->QueryInterface(iid, object);
            instance->Release();
            return hr;
        } else {
            ClassicCommand* instance = new (std::nothrow) ClassicCommand(target_);
            if (!instance) return E_OUTOFMEMORY;
            const HRESULT hr = instance->QueryInterface(iid, object);
            instance->Release();
            return hr;
        }
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) ++live_objects;
        else --live_objects;
        return S_OK;
    }
private:
    volatile LONG references_{1};
    const wchar_t* const target_;
    const bool cascade_;
};
}  // namespace

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        module_handle = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}
STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** object) {
    if (!object) return E_POINTER;
    *object = nullptr;
    const wchar_t* target = nullptr;
    if (clsid != kClassicCommandClsid && clsid != kQuickCascadeClsid) {
        bool known = false;
        for (const auto& item : kQuickTargets) {
            if (clsid == item.clsid) {
                target = item.format;
                known = true;
                break;
            }
        }
        if (!known) return CLASS_E_CLASSNOTAVAILABLE;
    }
    CommandFactory* factory = new (std::nothrow) CommandFactory(target, clsid == kQuickCascadeClsid);
    if (!factory) return E_OUTOFMEMORY;
    const HRESULT hr = factory->QueryInterface(iid, object);
    factory->Release();
    return hr;
}
STDAPI DllCanUnloadNow() {
    return live_objects.load() == 0 ? S_OK : S_FALSE;
}
