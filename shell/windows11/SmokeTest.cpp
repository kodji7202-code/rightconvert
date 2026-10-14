// In-process COM smoke test: no Explorer restart or package registration needed.
#include <windows.h>
#include <shobjidl.h>
#include <cstdio>
#include <cwchar>

using GetClassObject = HRESULT(STDAPICALLTYPE*)(REFCLSID, REFIID, void**);
using CanUnload = HRESULT(STDAPICALLTYPE*)();

int wmain(int argc, wchar_t** argv) {
    if (argc != 2) {
        std::fwprintf(stderr, L"Usage: RightConvertExplorerSmoke.exe <dll path>\n");
        return 2;
    }
    HMODULE library = LoadLibraryW(argv[1]);
    if (!library) return 3;
    auto get_class = reinterpret_cast<GetClassObject>(GetProcAddress(library, "DllGetClassObject"));
    auto can_unload = reinterpret_cast<CanUnload>(GetProcAddress(library, "DllCanUnloadNow"));
    if (!get_class || !can_unload) return 4;

    CLSID clsid = {0xaa7fb3f0, 0x5e6c, 0x4d75, {0xa2, 0x2f, 0xc0, 0xa8, 0xd7, 0xa6, 0x32, 0x11}};
    IClassFactory* factory = nullptr;
    HRESULT hr = get_class(clsid, IID_IClassFactory, reinterpret_cast<void**>(&factory));
    if (FAILED(hr) || !factory) return 5;
    IExplorerCommand* command = nullptr;
    hr = factory->CreateInstance(nullptr, IID_IExplorerCommand, reinterpret_cast<void**>(&command));
    if (FAILED(hr) || !command) return 6;

    PWSTR title = nullptr;
    hr = command->GetTitle(nullptr, &title);
    bool ok = SUCCEEDED(hr) && title && wcscmp(title, L"Convert with RightConvert") == 0;
    CoTaskMemFree(title);
    EXPCMDSTATE state{};
    hr = command->GetState(nullptr, FALSE, &state);
    ok = ok && SUCCEEDED(hr) && state == ECS_HIDDEN;
    GUID actual{};
    hr = command->GetCanonicalName(&actual);
    ok = ok && SUCCEEDED(hr) && actual == clsid;
    EXPCMDFLAGS flags{};
    hr = command->GetFlags(&flags);
    ok = ok && SUCCEEDED(hr) && flags == ECF_DEFAULT;

    command->Release();
    factory->Release();
    ok = ok && can_unload() == S_OK;
    FreeLibrary(library);
    if (!ok) return 7;
    std::puts("RightConvertExplorer COM smoke test passed.");
    return 0;
}
