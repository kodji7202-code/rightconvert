// Integration regression for the native classic batch command.
// Uses a private temporary fixture, no COM registration or Explorer mutation.
#include <windows.h>
#include <shlobj.h>
#include <shobjidl.h>

#include <cstdio>
#include <string>
#include <vector>

using ClassObjectProc = HRESULT(STDAPICALLTYPE*)(REFCLSID, REFIID, void**);
using CanUnloadProc = HRESULT(STDAPICALLTYPE*)();

std::wstring OwnExe() {
    std::vector<wchar_t> buffer(32768);
    DWORD count = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    return count ? std::wstring(buffer.data(), count) : std::wstring();
}

bool Exists(const std::wstring& path) {
    return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}

bool AwaitExactArguments(const std::wstring& report, const std::wstring& expected) {
    for (int attempt = 0; attempt < 100; ++attempt) {
        HANDLE file = CreateFileW(report.c_str(), GENERIC_READ, FILE_SHARE_READ,
                                  nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) {
            Sleep(30);
            continue;
        }
        const DWORD length = GetFileSize(file, nullptr);
        if (length == INVALID_FILE_SIZE || (length % sizeof(wchar_t)) != 0) {
            CloseHandle(file);
            return false;
        }
        std::vector<wchar_t> bytes(length / sizeof(wchar_t) + 1);
        DWORD read = 0;
        const BOOL ok = ReadFile(file, bytes.data(), length, &read, nullptr);
        CloseHandle(file);
        if (!ok || read != length) return false;
        return std::wstring(bytes.data(), read / sizeof(wchar_t)) == expected;
    }
    return false;
}

int wmain(int argc, wchar_t** argv) {
    if (argc >= 2 && wcscmp(argv[1], L"--files") == 0) {
        // Child process pretending to be RightConvert.exe, to observe exact
        // argv without launching a graphical conversion window.
        wchar_t report[32768]{};
        if (!GetEnvironmentVariableW(L"RIGHTCONVERT_CLASSIC_TEST_REPORT", report, 32768)) return 9;
        HANDLE out = CreateFileW(report, GENERIC_WRITE, 0, nullptr,
                                 CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (out == INVALID_HANDLE_VALUE) return 10;
        for (int i = 2; i < argc; ++i) {
            DWORD written = 0;
            WriteFile(out, argv[i], static_cast<DWORD>(wcslen(argv[i]) * sizeof(wchar_t)),
                      &written, nullptr);
            const wchar_t separator = L'\n';
            WriteFile(out, &separator, sizeof(separator), &written, nullptr);
        }
        CloseHandle(out);
        return 0;
    }
    if (argc != 2) return 2;
    if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) return 25;

    wchar_t temporary[MAX_PATH]{};
    if (!GetTempPathW(MAX_PATH, temporary)) return 3;
    GUID guid{};
    if (FAILED(CoCreateGuid(&guid))) return 4;
    wchar_t suffix[80]{};
    swprintf_s(suffix, L"RightConvertClassic-%08x%04x", guid.Data1, guid.Data2);
    std::wstring folder = std::wstring(temporary) + suffix;
    if (!CreateDirectoryW(folder.c_str(), nullptr)) return 5;
    const std::wstring dll = folder + L"\\RightConvertClassic.dll";
    const std::wstring exe = folder + L"\\RightConvert.exe";
    const std::wstring report = folder + L"\\args.txt";
    if (!CopyFileW(argv[1], dll.c_str(), TRUE) || !CopyFileW(OwnExe().c_str(), exe.c_str(), TRUE)) {
        return 6;
    }
    SetEnvironmentVariableW(L"RIGHTCONVERT_CLASSIC_TEST_REPORT", report.c_str());

    HMODULE library = LoadLibraryW(dll.c_str());
    if (!library) return 7;
    auto get_factory = reinterpret_cast<ClassObjectProc>(GetProcAddress(library, "DllGetClassObject"));
    auto can_unload = reinterpret_cast<CanUnloadProc>(GetProcAddress(library, "DllCanUnloadNow"));
    if (!get_factory || !can_unload) return 8;

    CLSID clsid = {0xd172f75d, 0x5505, 0x42a8, {0xae, 0x5a, 0x38, 0x0b, 0x04, 0x06, 0xb4, 0x8c}};
    IClassFactory* unknown_factory = nullptr;
    GUID unknown_clsid{};
    if (get_factory(unknown_clsid, IID_IClassFactory,
                    reinterpret_cast<void**>(&unknown_factory)) != CLASS_E_CLASSNOTAVAILABLE ||
        unknown_factory) return 32;
    IClassFactory* factory = nullptr;
    if (FAILED(get_factory(clsid, IID_IClassFactory, reinterpret_cast<void**>(&factory)))) return 11;
    IExecuteCommand* command = nullptr;
    if (FAILED(factory->CreateInstance(nullptr, IID_IExecuteCommand, reinterpret_cast<void**>(&command)))) return 12;
    IObjectWithSelection* selected = nullptr;
    if (FAILED(command->QueryInterface(IID_IObjectWithSelection, reinterpret_cast<void**>(&selected)))) return 13;
    // QueryInterface and empty Execute must behave correctly even without a
    // Shell selection. This also confirms the handler doesn't create a process.
    if (command->Execute() != E_INVALIDARG || Exists(report)) return 14;

    std::vector<std::wstring> filenames;
    std::vector<PIDLIST_ABSOLUTE> pidls;
    for (int i = 0; i < 21; ++i) {
        std::wstring path = folder + L"\\";
        path += L"fișier (" + std::to_wstring(i) + L") & Test ^ 日本語.txt";
        HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ,
                                   nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) return 15;
        CloseHandle(file);
        PIDLIST_ABSOLUTE pidl = ILCreateFromPathW(path.c_str());
        if (!pidl) return 16;
        pidls.push_back(pidl);
        filenames.push_back(path);
    }
    IShellItemArray* items = nullptr;
    HRESULT hr = SHCreateShellItemArrayFromIDLists(static_cast<UINT>(pidls.size()),
                                                  const_cast<PCIDLIST_ABSOLUTE*>(pidls.data()), &items);
    for (auto* pidl : pidls) ILFree(pidl);
    if (FAILED(hr) || !items) return 17;
    hr = selected->SetSelection(items);
    if (FAILED(hr)) return 18;
    hr = command->Execute();
    if (FAILED(hr)) return 19;
    std::wstring expected;
    for (const auto& path : filenames) expected += path + L"\n";
    if (!AwaitExactArguments(report, expected)) {
        std::fwprintf(stderr, L"Batch arguments were truncated or corrupted.\n");
        return 23;
    }

    // Each fixed target must preserve all 21 Unicode paths in one process and
    // append precisely the agreed CLI switches, without touching user registry.
    struct QuickCase {
        GUID clsid;
        const wchar_t* format;
    };
    constexpr QuickCase quick[] = {
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
        {{0xe82a6cd6, 0x85c7, 0x4d15, {0x9e, 0xd2, 0x1a, 0x44, 0xa6, 0x50, 0x07, 0x11}}, L"pngzip"}
    };
    for (const auto& test : quick) {
        if (!DeleteFileW(report.c_str())) return 26;
        IClassFactory* quick_factory = nullptr;
        if (FAILED(get_factory(test.clsid, IID_IClassFactory, reinterpret_cast<void**>(&quick_factory)))) return 27;
        IExecuteCommand* quick_command = nullptr;
        if (FAILED(quick_factory->CreateInstance(nullptr, IID_IExecuteCommand,
                                                  reinterpret_cast<void**>(&quick_command)))) return 28;
        IObjectWithSelection* quick_selection = nullptr;
        if (FAILED(quick_command->QueryInterface(IID_IObjectWithSelection,
                                                  reinterpret_cast<void**>(&quick_selection)))) return 29;
        // Parameters provided by Explorer must not override the fixed format
        // configured by the trusted verb CLSID.
        if (FAILED(quick_command->SetParameters(L"--target unknown --files C:\\wrong.txt"))) return 33;
        if (FAILED(quick_selection->SetSelection(items)) || FAILED(quick_command->Execute())) return 30;
        const std::wstring quick_expected = expected + L"--target\n" + test.format + L"\n--direct\n--notify\n";
        if (!AwaitExactArguments(report, quick_expected)) {
            std::fwprintf(stderr, L"Quick-target arguments were corrupted for %ls.\n", test.format);
            return 31;
        }
        quick_selection->Release();
        quick_command->Release();
        quick_factory->Release();
    }

    items->Release();
    selected->Release();
    command->Release();
    factory->Release();
    if (can_unload() != S_OK) return 24;
    FreeLibrary(library);
    DeleteFileW(report.c_str());
    DeleteFileW(exe.c_str());
    DeleteFileW(dll.c_str());
    for (const auto& path : filenames) DeleteFileW(path.c_str());
    RemoveDirectoryW(folder.c_str());
    std::puts("PASS: default GUI and 11 fixed direct targets; 21 Unicode paths per launch.");
    return 0;
}
