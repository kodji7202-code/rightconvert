// Windows 11 modern File Explorer menu extension for RightConvert.
// COM activation is supplied by the signed sparse MSIX package, not regsvr32.
#include <windows.h>
#include <shobjidl.h>
#include <shlwapi.h>

#include <atomic>
#include <new>
#include <string>
#include <vector>

namespace {

// Must be identical to the com:Class and desktop5:Verb CLSID in AppxManifest.xml.
constexpr CLSID kRightConvertClsid = {
    0xaa7fb3f0, 0x5e6c, 0x4d75, {0xa2, 0x2f, 0xc0, 0xa8, 0xd7, 0xa6, 0x32, 0x11}
};

std::atomic<long> live_objects{0};
HMODULE module_instance = nullptr;

std::wstring QuoteArgument(const std::wstring& input) {
    // Microsoft C runtime quoting rules: preserve Unicode, backslashes and quotes.
    std::wstring result = L"\"";
    size_t backslashes = 0;
    for (wchar_t ch : input) {
        if (ch == L'\\') {
            ++backslashes;
            continue;
        }
        if (ch == L'"') {
            result.append(backslashes * 2 + 1, L'\\');
        } else {
            result.append(backslashes, L'\\');
        }
        backslashes = 0;
        result += ch;
    }
    result.append(backslashes * 2, L'\\');
    result += L'"';
    return result;
}

std::wstring ExecutablePath() {
    if (!module_instance) return {};
    std::vector<wchar_t> buffer(MAX_PATH);
    for (;;) {
        DWORD length = GetModuleFileNameW(module_instance, buffer.data(),
                                         static_cast<DWORD>(buffer.size()));
        if (length == 0) return {};
        if (length < buffer.size()) {
            std::wstring dll_path(buffer.data(), length);
            const size_t separator = dll_path.find_last_of(L"\\/");
            if (separator == std::wstring::npos) return {};
            return dll_path.substr(0, separator + 1) + L"RightConvert.exe";
        }
        if (buffer.size() >= 32768) return {};
        buffer.resize(buffer.size() * 2);
    }
}

HRESULT StartRightConvert(const std::wstring& exe, const std::wstring& arguments) {
    std::wstring mutable_cmdline = QuoteArgument(exe) + L" --files" + arguments;
    if (mutable_cmdline.size() >= 32767) return HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE);

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(exe.c_str(), &mutable_cmdline[0], nullptr, nullptr,
                        FALSE, 0, nullptr, nullptr, &startup, &process)) {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return S_OK;
}

class ExplorerCommand final : public IExplorerCommand {
public:
    ExplorerCommand() { ++live_objects; }
    ~ExplorerCommand() { --live_objects; }

    IFACEMETHODIMP QueryInterface(REFIID id, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (id == IID_IUnknown || id == IID_IExplorerCommand) {
            *value = static_cast<IExplorerCommand*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }

    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG remaining = static_cast<ULONG>(InterlockedDecrement(&references_));
        if (remaining == 0) delete this;
        return remaining;
    }

    IFACEMETHODIMP GetTitle(IShellItemArray*, LPWSTR* title) override {
        if (!title) return E_POINTER;
        return SHStrDupW(L"Convert with RightConvert", title);
    }

    IFACEMETHODIMP GetIcon(IShellItemArray*, LPWSTR* icon) override {
        if (!icon) return E_POINTER;
        *icon = nullptr;
        std::wstring exe = ExecutablePath();
        if (exe.empty()) return E_NOTIMPL;
        return SHStrDupW(exe.c_str(), icon);
    }

    IFACEMETHODIMP GetToolTip(IShellItemArray*, LPWSTR* tip) override {
        if (!tip) return E_POINTER;
        return SHStrDupW(L"Convert selected files locally with RightConvert", tip);
    }

    IFACEMETHODIMP GetCanonicalName(GUID* guid) override {
        if (!guid) return E_POINTER;
        *guid = kRightConvertClsid;
        return S_OK;
    }

    IFACEMETHODIMP GetState(IShellItemArray* selection, BOOL, EXPCMDSTATE* state) override {
        if (!state) return E_POINTER;
        *state = ECS_HIDDEN;
        if (!selection) return S_OK;
        DWORD count = 0;
        if (SUCCEEDED(selection->GetCount(&count)) && count > 0) {
            *state = ECS_ENABLED;
        }
        return S_OK;
    }

    IFACEMETHODIMP Invoke(IShellItemArray* selection, IBindCtx*) override {
        if (!selection) return E_INVALIDARG;
        const std::wstring exe = ExecutablePath();
        if (exe.empty()) return E_FAIL;

        DWORD count = 0;
        HRESULT hr = selection->GetCount(&count);
        if (FAILED(hr)) return hr;
        if (count == 0) return E_INVALIDARG;

        std::wstring arguments;
        bool launched = false;
        for (DWORD i = 0; i < count; ++i) {
            IShellItem* item = nullptr;
            hr = selection->GetItemAt(i, &item);
            if (FAILED(hr)) return hr;
            PWSTR path = nullptr;
            hr = item->GetDisplayName(SIGDN_FILESYSPATH, &path);
            item->Release();
            if (FAILED(hr)) return hr;
            std::wstring quoted = L" " + QuoteArgument(path);
            CoTaskMemFree(path);
            // Windows command lines have a ~32K UTF-16 code-unit limit. Split
            // exceptionally large multi-file selections into several launches.
            const size_t overhead = QuoteArgument(exe).size() + 8;
            if (!arguments.empty() && arguments.size() + quoted.size() + overhead >= 32767) {
                hr = StartRightConvert(exe, arguments);
                if (FAILED(hr)) return hr;
                launched = true;
                arguments.clear();
            }
            if (quoted.size() + overhead >= 32767) return HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE);
            arguments += quoted;
        }
        if (!arguments.empty()) {
            hr = StartRightConvert(exe, arguments);
            if (FAILED(hr)) return hr;
            launched = true;
        }
        return launched ? S_OK : E_INVALIDARG;
    }

    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
        if (!flags) return E_POINTER;
        *flags = ECF_DEFAULT;
        return S_OK;
    }

    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** children) override {
        if (!children) return E_POINTER;
        *children = nullptr;
        return E_NOTIMPL;
    }

private:
    volatile LONG references_{1};
};

class CommandFactory final : public IClassFactory {
public:
    CommandFactory() { ++live_objects; }
    ~CommandFactory() { --live_objects; }

    IFACEMETHODIMP QueryInterface(REFIID id, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (id == IID_IUnknown || id == IID_IClassFactory) {
            *value = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG remaining = static_cast<ULONG>(InterlockedDecrement(&references_));
        if (remaining == 0) delete this;
        return remaining;
    }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID id, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        ExplorerCommand* command = new (std::nothrow) ExplorerCommand();
        if (!command) return E_OUTOFMEMORY;
        HRESULT hr = command->QueryInterface(id, value);
        command->Release();
        return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) ++live_objects;
        else --live_objects;
        return S_OK;
    }
private:
    volatile LONG references_{1};
};

} // namespace

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        module_instance = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** value) {
    if (!value) return E_POINTER;
    *value = nullptr;
    if (clsid != kRightConvertClsid) return CLASS_E_CLASSNOTAVAILABLE;
    CommandFactory* factory = new (std::nothrow) CommandFactory();
    if (!factory) return E_OUTOFMEMORY;
    HRESULT hr = factory->QueryInterface(iid, value);
    factory->Release();
    return hr;
}

STDAPI DllCanUnloadNow() {
    return live_objects.load() == 0 ? S_OK : S_FALSE;
}
