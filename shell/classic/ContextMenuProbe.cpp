// Acceptance probe for an *installed* RightConvert (used by
// installer/test-setup-install-e2e.ps1). Builds a file's context menu the way
// Explorer does and optionally invokes one entry by its exact caption.
//
//   RightConvertMenuProbe.exe --shell   <file> [caption-to-invoke]
//       Full Shell item menu via IShellFolder::GetUIObjectOf(IContextMenu),
//       so every registered static verb and shellex handler participates.
//   RightConvertMenuProbe.exe --handler <clsid> <file> [caption-to-invoke]
//       Only the registered handler class, activated through COM (HKCU
//       registration) and initialized with the Shell's own IDataObject.
//
//   RightConvertMenuProbe.exe --unelevated <any of the modes above>
//       Re-runs the probe with a standard-user (SAFER_LEVELID_NORMALUSER),
//       medium-integrity copy of the caller's token, i.e. the token Explorer
//       has on a normal desktop. Windows does not load shell extensions that
//       are registered only per user (HKCU\Software\Classes) into an elevated
//       process, so an elevated CI shell cannot see them; this mode builds the
//       menu the way the user's Explorer does.
//
// Prints "TOKEN: elevated=<0|1> integrity=0x<rid>" first, then one
// "ITEM: <caption>" line per menu entry and "INVOKED: <caption>" after a
// successful InvokeCommand. Never registers anything itself.
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <wincrypt.h>
#include <winsafer.h>

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

std::wstring QuoteArgument(const std::wstring& argument) {
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

void PrintTokenState() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return;
    TOKEN_ELEVATION elevation{};
    DWORD size = 0;
    const BOOL have_elevation = GetTokenInformation(token, TokenElevation, &elevation, sizeof(elevation), &size);
    alignas(TOKEN_MANDATORY_LABEL) BYTE buffer[256]{};
    DWORD integrity = 0;
    if (GetTokenInformation(token, TokenIntegrityLevel, buffer, sizeof(buffer), &size)) {
        const auto* label = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buffer);
        const UCHAR count = *GetSidSubAuthorityCount(label->Label.Sid);
        if (count) integrity = *GetSidSubAuthority(label->Label.Sid, count - 1u);
    }
    CloseHandle(token);
    std::printf("TOKEN: elevated=%s integrity=0x%04lx\n",
                have_elevation ? (elevation.TokenIsElevated ? "1" : "0") : "?",
                static_cast<unsigned long>(integrity));
    std::fflush(stdout);
}

// Runs this probe again, arguments after "--unelevated" unchanged, with a
// standard-user medium-integrity restricted copy of the current token.
int RelaunchUnelevated(int argc, wchar_t** argv) {
    SAFER_LEVEL_HANDLE level = nullptr;
    if (!SaferCreateLevel(SAFER_SCOPEID_USER, SAFER_LEVELID_NORMALUSER, SAFER_LEVEL_OPEN, &level, nullptr)) {
        std::fprintf(stderr, "SaferCreateLevel failed: %lu\n", GetLastError());
        return 30;
    }
    HANDLE token = nullptr;
    const BOOL computed = SaferComputeTokenFromLevel(level, nullptr, &token, 0, nullptr);
    const DWORD compute_error = GetLastError();
    SaferCloseLevel(level);
    if (!computed) {
        std::fprintf(stderr, "SaferComputeTokenFromLevel failed: %lu\n", compute_error);
        return 31;
    }
    SID_IDENTIFIER_AUTHORITY authority = SECURITY_MANDATORY_LABEL_AUTHORITY;
    PSID medium = nullptr;
    if (!AllocateAndInitializeSid(&authority, 1, SECURITY_MANDATORY_MEDIUM_RID,
                                  0, 0, 0, 0, 0, 0, 0, &medium)) {
        CloseHandle(token);
        return 32;
    }
    TOKEN_MANDATORY_LABEL label{};
    label.Label.Attributes = SE_GROUP_INTEGRITY;
    label.Label.Sid = medium;
    const BOOL lowered = SetTokenInformation(token, TokenIntegrityLevel, &label,
                                             sizeof(label) + GetLengthSid(medium));
    const DWORD lower_error = GetLastError();
    FreeSid(medium);
    if (!lowered) {
        CloseHandle(token);
        std::fprintf(stderr, "Setting medium integrity failed: %lu\n", lower_error);
        return 33;
    }
    std::vector<wchar_t> self(32768);
    const DWORD length = GetModuleFileNameW(nullptr, self.data(), static_cast<DWORD>(self.size()));
    if (!length || length >= self.size()) {
        CloseHandle(token);
        return 34;
    }
    std::wstring command = QuoteArgument(self.data());
    for (int index = 2; index < argc; ++index) command += L" " + QuoteArgument(argv[index]);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    for (HANDLE handle : {startup.hStdInput, startup.hStdOutput, startup.hStdError}) {
        if (handle && handle != INVALID_HANDLE_VALUE)
            SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT);
    }
    std::fflush(stdout);
    std::fflush(stderr);
    PROCESS_INFORMATION process{};
    if (!CreateProcessAsUserW(token, self.data(), &command[0], nullptr, nullptr, TRUE, 0,
                              nullptr, nullptr, &startup, &process)) {
        const DWORD error = GetLastError();
        CloseHandle(token);
        std::fprintf(stderr, "CreateProcessAsUserW (standard user) failed: %lu\n", error);
        return 35;
    }
    CloseHandle(token);
    CloseHandle(process.hThread);
    DWORD code = 36;
    if (WaitForSingleObject(process.hProcess, 180000) == WAIT_OBJECT_0) {
        GetExitCodeProcess(process.hProcess, &code);
    } else {
        TerminateProcess(process.hProcess, 37);
        std::fprintf(stderr, "Standard-user probe timed out\n");
    }
    CloseHandle(process.hProcess);
    return static_cast<int>(code);
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc >= 3 && wcscmp(argv[1], L"--unelevated") == 0) return RelaunchUnelevated(argc, argv);
    PrintTokenState();
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
    } else if ((argc == 4 || argc == 5) && wcscmp(argv[1], L"--handler") == 0) {
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
            result = ListAndInvoke(menu, argc == 5 ? argv[4] : nullptr);
        }
        if (data) data->Release();
        if (init) init->Release();
        if (menu) menu->Release();
    }
    CoUninitialize();
    return result;
}
