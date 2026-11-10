#import <Cocoa/Cocoa.h>
#include "4DPluginAPI.h"
#include "PrivateTypes.h"
#include "EntryPoints.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <set>
#include <string>

// Never install a real monitor or inspect an event, even through PluginMain.
static int monitors = 0;
@interface OwnershipEvent : NSObject
+ (id)addLocalMonitorForEventsMatchingMask:(NSEventMask)mask
                                  handler:(OwnershipEvent *(^)(OwnershipEvent *))handler;
+ (void)removeMonitor:(id)monitor;
- (NSString *)charactersIgnoringModifiers;
@end
@implementation OwnershipEvent
+ (id)addLocalMonitorForEventsMatchingMask:(NSEventMask)mask
                                  handler:(OwnershipEvent *(^)(OwnershipEvent *))handler
{
    ++monitors;
    return [NSNull null];
}
+ (void)removeMonitor:(id)monitor { --monitors; }
- (NSString *)charactersIgnoringModifiers { std::abort(); }
@end
#define NSEvent OwnershipEvent
#include "4DPlugin-GET-TEXT-INPUT.cpp"
#undef NSEvent
#include "ARRAY_TEXT.h"
#include "C_BLOB.h"
#include "4DPlugin-JSON.h"

using Text = std::basic_string<PA_Unichar>;
static std::map<PA_Unichar *, size_t> strings;
static std::map<PA_Handle, size_t> handles;
struct Ref {
    int owners = 1;
    int kind;
    std::map<Text, PA_Variable> properties;
    explicit Ref(int type) : kind(type) {}
};
static std::set<Ref *> refs;
static bool nullEmpty = false;
static bool failHandle = false;
static bool failStringifyHandle = false;
static bool failGetter = false;
static PA_Variable callbackResult = {};
static PA_Variable stringifyResult = {};
static Text expectedText, expectedWorker;
static int methodCalls = 0, failedReads = 0;

static Text text(const char16_t *value)
{
    Text result;
    while(*value) result.push_back(*value++);
    return result;
}

static std::string utf8Fixture(const Text &value)
{
    if(value == text(u"ASCII")) return "ASCII";
    if(value == text(u"\u65e5\u672c")) return "\xE6\x97\xA5\xE6\x9C\xAC";
    if(value == text(u"\U0001F642")) return "\xF0\x9F\x99\x82";
    if(value.empty()) return "";
    std::abort();
}

static void check(bool condition, const char *name)
{
    if(!condition) {
        std::cerr << "FAIL: " << name << "\n";
        std::exit(1);
    }
}

static Text content(const PA_Unistring &value)
{
    if(!value.fString) {
        check(value.fLength == 0, "null empty length");
        return {};
    }
    PA_Unichar *buffer = value.fString;
    auto it = strings.find(buffer);
    check(it != strings.end(), "live string owner");
    check(it->second == static_cast<size_t>(value.fLength), "UTF16 length units");
    check(value.fString[value.fLength] == 0, "UTF16 terminator within capacity");
    return Text(value.fString, value.fLength);
}

static PA_Unistring makeString(const Text &value)
{
    PA_Unistring result = {};
    if(value.empty() && nullEmpty) return result;
    PA_Unichar *buffer = static_cast<PA_Unichar *>(std::malloc((value.size() + 1) * sizeof(PA_Unichar)));
    result.fString = buffer;
    check(result.fString != nullptr, "fixture string allocation");
    result.fLength = static_cast<PA_long32>(value.size());
    std::memcpy(result.fString, value.c_str(), (value.size() + 1) * sizeof(PA_Unichar));
    strings.emplace(buffer, value.size());
    return result;
}

static void disposeString(PA_Unistring &value)
{
    content(value);
    if(value.fString) {
        PA_Unichar *buffer = value.fString;
        check(strings.erase(buffer) == 1, "single string release");
        std::free(buffer);
    }
    value = {};
}

static Ref *makeRef(int kind)
{
    Ref *result = new Ref(kind);
    refs.insert(result);
    return result;
}

static void releaseRef(void *value);
static void clear(PA_Variable &value)
{
    switch(value.fType) {
        case eVK_Unistring: disposeString(value.uValue.fString); break;
        case eVK_Object: releaseRef(value.uValue.fObject); break;
        case eVK_Collection: releaseRef(value.uValue.fCollection); break;
        case eVK_Picture: releaseRef(value.uValue.fPicture); break;
        case eVK_ArrayUnicode: {
            PA_Handle handle = value.uValue.fArray.fData;
            if(handle) {
                check(handles.count(handle), "live array handle");
                check(handles.at(handle) >= (value.uValue.fArray.fNbElements + 1) * sizeof(PA_Unistring),
                      "array count within capacity");
                auto elements = reinterpret_cast<PA_Unistring *>(*handle);
                for(int i = 0; i <= value.uValue.fArray.fNbElements; ++i) disposeString(elements[i]);
                handles.erase(handle);
                std::free(*handle);
                delete handle;
            }
            break;
        }
        case eVK_Blob:
            if(value.uValue.fBlob.fHandle) PA_DisposeHandle(value.uValue.fBlob.fHandle);
            break;
        case eVK_Undefined: case eVK_Null: case eVK_Longint:
        case eVK_Boolean: case eVK_Real: break;
        default: check(false, "never clear raw-kind-zero/failed output");
    }
    value = {};
    value.fType = eVK_Undefined;
}

static void releaseRef(void *value)
{
    if(!value) return;
    auto ref = static_cast<Ref *>(value);
    check(refs.count(ref), "live reference");
    if(--ref->owners == 0) {
        refs.erase(ref);
        for(auto &property : ref->properties) clear(property.second);
        delete ref;
    }
}

static PA_Variable copy(PA_Variable value)
{
    switch(value.fType) {
        case eVK_Unistring: value.uValue.fString = makeString(content(value.uValue.fString)); break;
        case eVK_Object: case eVK_Collection: case eVK_Picture: {
            auto ref = static_cast<Ref *>(value.uValue.fObject);
            if(ref) {
                check(refs.count(ref), "retain live ref");
                ++ref->owners;
            }
            break;
        }
        default: break;
    }
    return value;
}

static PA_Variable stringVariable(const Text &value)
{
    PA_Variable result = {};
    PA_Unistring owner = makeString(value);
    PA_SetStringVariable(&result, &owner);
    return result;
}

static PA_Variable referenceVariable(int kind)
{
    PA_Variable result = {};
    result.fType = kind;
    result.uValue.fObject = makeRef(kind);
    return result;
}

static void host(short selector, EngineBlock *eb)
{
    switch(selector) {
        case EX_CREATE_UNISTRING:
            eb->fUniString1 = makeString(Text(reinterpret_cast<PA_Unichar *>(eb->fHandle)));
            return;
        case EX_DISPOSE_UNISTRING: disposeString(eb->fUniString1); return;
        case EX_SET_UNISTRING: {
            Text next(reinterpret_cast<PA_Unichar *>(eb->fHandle));
            disposeString(eb->fUniString1);
            eb->fUniString1 = makeString(next);
            return;
        }
        case EX_CLEAR_VARIABLE: clear(*static_cast<PA_Variable *>(eb->fPtr1)); return;
        case EX_VARIABLE_TO_STRING: {
            auto var = reinterpret_cast<PA_Variable *>(eb->fHandle);
            check(var->fType == eVK_Unistring, "never read raw-kind-zero/failed output");
            content(var->uValue.fString);
            eb->fUniString1 = var->uValue.fString;
            return;
        }
        case EX_CREATE_PICTURE: eb->fPicture = makeRef(eVK_Picture); return;
        case EX_DISPOSE_PICTURE: releaseRef(eb->fPicture); return;
        case EX_HANDLE_MANAGER: {
            switch(eb->fParam1) {
                case 1: {
                    if(failHandle) {
                        failHandle = false;
                        eb->fHandle = nullptr;
                        eb->fError = eER_NotEnoughMemory;
                        return;
                    }
                    auto handle = new char *;
                    size_t size = eb->fParam2;
                    *handle = static_cast<char *>(std::calloc(size ? size : 1, 1));
                    check(*handle, "fixture handle allocation");
                    handles.emplace(handle, size);
                    eb->fHandle = handle;
                    return;
                }
                case 2: {
                    PA_Handle handle = eb->fHandle;
                    check(handles.erase(handle) == 1, "single handle release");
                    std::free(*handle);
                    delete handle;
                    return;
                }
                case 3: {
                    PA_Handle handle = eb->fHandle;
                    check(handles.count(handle), "resize live handle");
                    size_t size = eb->fParam2;
                    *handle = static_cast<char *>(std::realloc(*handle, size ? size : 1));
                    check(*handle, "fixture handle resize");
                    handles.at(handle) = size;
                    return;
                }
                case 4: {
                    PA_Handle handle = eb->fHandle;
                    eb->fParam2 = handles.at(handle);
                    return;
                }
                case 5: {
                    PA_Handle handle = eb->fHandle;
                    check(handles.count(handle), "lock live handle");
                    eb->fParam3 = reinterpret_cast<sLONG_PTR>(*handle);
                    return;
                }
                case 6: return;
                default: check(false, "unexpected handle operation");
            }
            break;
        }
        case EX_GET_METHOD_ID: eb->fParam1 = 42; return;
        case EX_CALL_BY_PROCID: {
            check(eb->fParam1 == 42 && eb->fParam2 == 3, "callback identity/arity");
            auto vars = reinterpret_cast<PA_Variable *>(*eb->fHandle);
            check(content(vars[1].uValue.fString) == expectedText, "callback exact text");
            check(content(vars[2].uValue.fString) == expectedWorker, "callback exact worker");
            check(vars[1].uValue.fString.fString != vars[2].uValue.fString.fString ||
                  !vars[1].uValue.fString.fString, "callback independent arguments");
            check(vars[3].uValue.fLongint == 73, "callback window");
            vars[0] = copy(callbackResult);
            ++methodCalls;
            return;
        }
        case EX_RUN_IN_MAIN_PROCESS:
            reinterpret_cast<PA_RunInMainProcessProcPtr>(eb->fPtr1)(eb->fPtr2);
            return;
        case EX_SET_OBJ_VALUE: {
            auto ref = static_cast<Ref *>(eb->fPtr1);
            check(refs.count(ref), "set live object");
            Text key = content(eb->fUniString1);
            auto old = ref->properties.find(key);
            if(old != ref->properties.end()) clear(old->second);
            ref->properties[key] = copy(*static_cast<PA_Variable *>(eb->fPtr2));
            return;
        }
        case EX_GET_OBJ_VALUE: {
            if(failGetter) {
                // A failed engine call need not initialize this output. Poison it
                // to make any adoption/access/cleanup deterministic in the test.
                std::memset(eb->fPtr2, 0xA5, sizeof(PA_Variable));
                eb->fError = eER_NotEnoughMemory;
                ++failedReads;
                return;
            }
            auto ref = static_cast<Ref *>(eb->fPtr1);
            check(refs.count(ref), "get live object");
            *static_cast<PA_Variable *>(eb->fPtr2) = copy(ref->properties.at(content(eb->fUniString1)));
            return;
        }
        case EX_EXECUTE_COMMAND_BY_ID: {
            auto vars = static_cast<PA_Variable **>(eb->fPtr1);
            auto result = static_cast<PA_Variable *>(eb->fPtr2);
            switch(eb->fParam1) {
                case 1231: {
                    auto ref = static_cast<Ref *>(vars[0]->uValue.fObject);
                    PA_SetBooleanVariable(result, ref->properties.count(content(vars[1]->uValue.fString)) != 0);
                    return;
                }
                case 1225:
                    *result = copy(*vars[0]);
                    if(failStringifyHandle) failHandle = true;
                    return;
                case 1217: *result = copy(stringifyResult); return;
                default: check(false, "unexpected command");
            }
            break;
        }
        default:
            std::cerr << "Unexpected engine selector: " << selector << "\n";
            std::abort();
    }
}

static void balanced(const char *name)
{
    if(!strings.empty() || !handles.empty() || !refs.empty()) {
        std::cerr << "Outstanding strings=" << strings.size() << " handles=" << handles.size()
                  << " refs=" << refs.size() << "\n";
        check(false, name);
    }
}

static void callbackCase()
{
    for(bool empty : {false, true}) {
        nullEmpty = empty;
        for(const Text &fixture : {text(u"ASCII"), text(u"\u65e5\u672c"), text(u"\U0001F642"), Text()}) {
            for(int kind : {eVK_Unistring, eVK_Object, eVK_Collection, eVK_Picture, eVK_Undefined}) {
                callbackResult = kind == eVK_Unistring ? stringVariable(fixture) :
                    kind == eVK_Undefined ? PA_CreateVariable(eVK_Undefined) : referenceVariable(kind);
                expectedText = fixture;
                expectedWorker = fixture;
                Wait::buffer = fixture;
                Wait::actionWorker = fixture;
                Wait::actionMethod = text(u"synthetic_callback");
                Wait::actionWindow = 73;
                Wait::call();
                check(Wait::buffer.empty() && !Wait::shouldCall, "callback drains synthetic buffer");
                clear(callbackResult);
                balanced("callback-return-owner");
            }
        }
    }
    callbackResult = PA_CreateVariable(eVK_Undefined);
    failHandle = true;
    Wait::call();
    balanced("callback failed handle is Undefined and inputs released");
    check(methodCalls == 40, "callback invocation count");
}

static void settersCase()
{
    for(bool empty : {false, true}) {
        nullEmpty = empty;
        Ref *object = makeRef(eVK_Object);
        for(const char *fixture : {"ASCII", "\xE6\x97\xA5\xE6\x9C\xAC", "\xF0\x9F\x99\x82", ""}) {
            ob_set_s(object, "key", fixture);
            C_TEXT got;
            got.setUTF16String(&object->properties.at(text(u"key")).uValue.fString);
            CUTF8String bytes;
            got.copyUTF8String(&bytes);
            check(std::string(bytes.begin(), bytes.end()) == fixture, "exact narrow roundtrip");
            ob_set_s(object, L"key", fixture);
            got.setUTF16String(&object->properties.at(text(u"key")).uValue.fString);
            got.copyUTF8String(&bytes);
            check(std::string(bytes.begin(), bytes.end()) == fixture, "exact wide roundtrip");
        }
        for(const wchar_t *fixture : {L"ASCII", L"\u65e5\u672c", L"\U0001F642", L""}) {
            CUTF16String expected;
            json_wconv(fixture, &expected);
            const Text fixtures[] = {text(u"ASCII"), text(u"\u65e5\u672c"), text(u"\U0001F642"), Text()};
            size_t index = fixture[0] == L'A' ? 0 : fixture[0] == 0x65e5 ? 1 :
                fixture[0] == 0x1f642 ? 2 : 3;
            check(expected == fixtures[index], "exact UTF32 to UTF16 units");
            ob_set_a(object, L"key", fixture);
            check(content(object->properties.at(text(u"key")).uValue.fString) == expected,
                  "exact wchar roundtrip");
            ob_set_a(object, L"key", &expected);
            check(content(object->properties.at(text(u"key")).uValue.fString) == expected,
                  "exact UTF16 roundtrip");
        }
        ob_set_p(object, L"key", PA_CreatePicture(nullptr, 0));
        releaseRef(object);
        balanced("json-setter-overwritten-empty-owner");
    }
}

static void gettersCase()
{
    for(bool empty : {false, true}) {
        nullEmpty = empty;
        Ref *object = makeRef(eVK_Object);
        for(const Text &fixture : {text(u"ASCII"), text(u"\u65e5\u672c"), text(u"\U0001F642"), Text()}) {
            PA_Variable source = stringVariable(fixture);
            object->properties[text(u"key")] = copy(source);
            clear(source);
            CUTF8String output;
            check(ob_get_a(object, L"key", &output), "scalar string getter");
            check(std::string(output.begin(), output.end()) == utf8Fixture(fixture), "exact scalar string content");
            ob_get_b(object, L"key");
            ob_get_n(object, L"key");
            check(!ob_get_o(object, L"key") && !ob_get_c(object, L"key"), "reference mismatch");
            clear(object->properties.at(text(u"key")));
        }
        PA_Variable scalar = {};
        PA_SetBooleanVariable(&scalar, true);
        object->properties[text(u"key")] = scalar;
        check(ob_get_b(object, L"key"), "boolean value survives owner cleanup");
        PA_SetRealVariable(&scalar, 12.5);
        object->properties[text(u"key")] = scalar;
        check(ob_get_n(object, L"key") == 12.5, "real value survives owner cleanup");
        for(int kind : {eVK_Object, eVK_Collection, eVK_Picture}) {
            object->properties[text(u"key")] = referenceVariable(kind);
            CUTF8String unchanged = CUTF8String((const uint8_t *)"unchanged");
            check(ob_get_a(object, L"key", &unchanged), "existing mismatched string property");
            check(unchanged == CUTF8String((const uint8_t *)"unchanged"), "mismatch preserves output");
            ob_get_b(object, L"key");
            ob_get_n(object, L"key");
            PA_ObjectRef gotObject = ob_get_o(object, L"key");
            PA_CollectionRef gotCollection = ob_get_c(object, L"key");
            clear(object->properties.at(text(u"key")));
            if(kind == eVK_Object) {
                check(refs.count(static_cast<Ref *>(gotObject)), "owning object handoff survives source");
                PA_DisposeObject(gotObject);
            } else check(!gotObject, "object mismatch returns null");
            if(kind == eVK_Collection) {
                check(refs.count(static_cast<Ref *>(gotCollection)), "owning collection handoff survives source");
                PA_DisposeCollection(gotCollection);
            } else check(!gotCollection, "collection mismatch returns null");
        }
        releaseRef(object);
        balanced("json-getter-owned-result");
    }
}

static void failedGettersCase()
{
    for(bool empty : {false, true}) {
        nullEmpty = empty;
        Ref *object = makeRef(eVK_Object);
        object->properties[text(u"key")] = PA_CreateVariable(eVK_Undefined);
        failGetter = true;
        CUTF8String output = CUTF8String((const uint8_t *)"preserved");
        check(!ob_get_a(object, L"key", &output), "failed string getter reports failure before output access");
        check(output == CUTF8String((const uint8_t *)"preserved"), "failed getter preserves output");
        check(!ob_get_b(object, L"key") && ob_get_n(object, L"key") == 0 &&
              !ob_get_o(object, L"key") && !ob_get_c(object, L"key"), "failed getter defaults");
        failGetter = false;
        releaseRef(object);
        balanced("json-getter-failed-output");
    }
    check(failedReads == 10, "all failed getters exercised");
}

static void stringifyCase(bool failure)
{
    Ref *object = makeRef(eVK_Object);
    for(const Text &fixture : {text(u"ASCII"), text(u"\u65e5\u672c"), text(u"\U0001F642"), Text()}) {
        stringifyResult = stringVariable(fixture);
        CUTF8String output = CUTF8String((const uint8_t *)"preserved");
        failStringifyHandle = failure;
        ob_stringify(object, &output);
        failStringifyHandle = false;
        if(failure) check(output == CUTF8String((const uint8_t *)"preserved"), "failed command preserves output");
        else {
            check(std::string(output.begin(), output.end()) == utf8Fixture(fixture), "exact stringify text");
        }
        clear(stringifyResult);
    }
    if(!failure) {
        stringifyResult = referenceVariable(eVK_Collection);
        CUTF8String output;
        ob_stringify(object, &output);
        clear(stringifyResult);
    }
    releaseRef(object);
    balanced("json-stringify-command-result");
}

static void arrayCase()
{
    for(bool empty : {false, true}) {
        nullEmpty = empty;
        PA_Variable array = PA_CreateVariable(eVK_ArrayUnicode);
        PA_ResizeArray(&array, 4);
        int i = 0;
        for(const Text &fixture : {text(u"ASCII"), text(u"\u65e5\u672c"), text(u"\U0001F642"), Text(), text(u"tail")}) {
            PA_Unistring owner = makeString(fixture);
            PA_SetStringInArray(array, i++, &owner);
        }
        // Borrowed getters are copied into C++ storage before any mutation.
        ARRAY_TEXT wrapper;
        BytePtr params[] = {reinterpret_cast<BytePtr>(&array)};
        wrapper.fromParamAtIndex(params, 1);
        PA_ResizeArray(&array, 0);
        wrapper.toParamAtIndex(params, 1);
        check(array.uValue.fArray.fNbElements == 4, "wrapper restores array count");
        i = 0;
        for(const Text &fixture : {text(u"ASCII"), text(u"\u65e5\u672c"), text(u"\U0001F642"), Text(), text(u"tail")}) {
            check(content(PA_GetStringInArray(array, i++)) == fixture, "exact independent array output");
        }
        auto elements = reinterpret_cast<PA_Unistring *>(*array.uValue.fArray.fData);
        check(elements[1].fString != elements[2].fString, "independent element allocations");
        wrapper.toParamAtIndex(params, 1);
        PA_ResizeArray(&array, 1);
        PA_Unistring owner = makeString(text(u"rejected"));
        PA_Variable wrong = PA_CreateVariable(eVK_Longint);
        PA_SetStringInArray(wrong, 0, &owner);
        PA_SetStringInArray(array, -1, &owner);
        PA_SetStringInArray(array, 2, &owner);
        PA_Variable noData = {};
        noData.fType = eVK_ArrayUnicode;
        PA_SetStringInArray(noData, 0, &owner);
        PA_SetStringInArray(array, 0, nullptr);
        check(content(owner) == text(u"rejected"), "rejections retain caller owner");
        PA_DisposeUnistring(&owner);
        PA_ClearVariable(&array);
        balanced("array transfers replacement shrink clear rejection");
    }
}

static void scalarCase()
{
    for(bool empty : {false, true}) {
        nullEmpty = empty;
        for(const Text &fixture : {text(u"ASCII"), text(u"\u65e5\u672c"), text(u"\U0001F642"), Text()}) {
            PA_Unistring source = makeString(fixture);
            BytePtr params[] = {reinterpret_cast<BytePtr>(&source)};
            C_TEXT wrapper;
            wrapper.fromParamAtIndex(params, 1);
            disposeString(source);
            source = makeString(text(u"replacement"));
            CUTF16String saved;
            wrapper.copyUTF16String(&saved);
            check(saved == fixture, "scalar copies borrowed input before mutation");
            wrapper.toParamAtIndex(params, 1);
            check(content(source) == fixture, "scalar content setter replaces old owner");
            CUTF8String bytes;
            wrapper.copyUTF8String(&bytes);
            check(std::string(bytes.begin(), bytes.end()) == utf8Fixture(fixture), "scalar exact UTF8 bytes");
            PA_Unistring returned = makeString(text(u"old result"));
            PA_Unistring *result = &returned;
            wrapper.setReturn(reinterpret_cast<sLONG_PTR *>(&result));
            check(content(returned) == fixture, "scalar return content setter");
            if(!fixture.empty()) check(returned.fString != source.fString, "independent scalar outputs");
            disposeString(source);
            disposeString(returned);
            balanced("scalar content and borrowed copies");
        }
    }
}

static void dispatcherCase()
{
    PA_Unistring stop = makeString({});
    PA_Unistring method = makeString(text(u"synthetic_callback"));
    PA_Unistring worker = makeString(text(u"synthetic_worker"));
    PA_long32 window = 73;
    BytePtr values[] = {reinterpret_cast<BytePtr>(&stop), reinterpret_cast<BytePtr>(&method),
                        reinterpret_cast<BytePtr>(&worker), reinterpret_cast<BytePtr>(&window)};
    PluginBlock params = {};
    params.fParameters = values;
    PluginMain(1, &params);
    check(monitors == 0, "empty stop code installs no monitor");
    disposeString(stop);
    stop = makeString(text(u";"));
    PluginMain(1, &params);
    check(monitors == 1, "dispatcher uses only stub monitor");
    OnExit();
    check(monitors == 0, "exit releases stub monitor");
    disposeString(stop);
    disposeString(method);
    disposeString(worker);
    check(Wait::actionMethod == text(u"synthetic_callback") &&
          Wait::actionWorker == text(u"synthetic_worker"), "dispatcher copies survive borrowed input disposal");
    balanced("dispatcher borrowed input and stub monitor");
}

int main(int argc, char **argv)
{
    @autoreleasepool {
        check(argc == 2, "one named scenario");
        gCall4D = host;
        std::string scenario = argv[1];
        if(scenario == "callback-return-owner") callbackCase();
        else if(scenario == "json-setter-overwritten-empty-owner") settersCase();
        else if(scenario == "json-getter-owned-result") gettersCase();
        else if(scenario == "json-command-failure-sentinel") stringifyCase(true);
        else if(scenario == "json-getter-failed-output") failedGettersCase();
        else if(scenario == "json-stringify-success") stringifyCase(false);
        else if(scenario == "array-contract") arrayCase();
        else if(scenario == "scalar-content-contract") scalarCase();
        else if(scenario == "dispatcher-stubbed-input") dispatcherCase();
        else check(false, "known scenario");
        balanced("final owner balance");
        std::cout << "PASS: " << scenario << "\n";
    }
}
