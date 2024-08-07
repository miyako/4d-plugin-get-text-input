/* --------------------------------------------------------------------------------
 #
 #  4DPlugin-GET-TEXT-INPUT.cpp
 #	source generated by 4D Plugin Wizard
 #	Project : GET TEXT INPUT
 #	author : miyako
 #	2024/07/03
 #  
 # --------------------------------------------------------------------------------*/

#include "4DPlugin-GET-TEXT-INPUT.h"

namespace Wait
{
    BOOL isDying = FALSE;
    BOOL eventHookIsActive = FALSE;
    BOOL shouldCall = FALSE;
    
    HHOOK eventMonitor = NULL;
    
    PA_Unichar stopcode;
    CUTF16String actionMethod;
    CUTF16String actionWorker;
    PA_long32 actionWindow;
    CUTF16String buffer;

    void call()
    {
        CUTF16String text = buffer;
        
        buffer.clear();
        
        shouldCall = FALSE;
        
        PA_long32 methodId = PA_GetMethodID((PA_Unichar *)actionMethod.c_str());
        
        if(methodId)
        {
            PA_Variable args[3];
            
            args[0] = PA_CreateVariable(eVK_Unistring);
            PA_SetUnistring((&(args[0].uValue.fString)), (PA_Unichar *)text.c_str());
            
            args[1] = PA_CreateVariable(eVK_Unistring);
            PA_SetUnistring((&(args[1].uValue.fString)), (PA_Unichar *)actionWorker.c_str());
            
            args[2] = PA_CreateVariable(eVK_Longint);
            PA_SetLongintVariable(&args[2], actionWindow);
            
            PA_ExecuteMethodByID(methodId, args, 3);
            
            PA_ClearVariable(&args[0]);
            PA_ClearVariable(&args[1]);
            PA_ClearVariable(&args[2]);
            
        }
        
    }
    
#if VERSIONMAC
#if USE_NSEVENT
#else
    static OSStatus onEventCall(EventHandlerCallRef inCaller, EventRef inEvent, void* inUserData)
    {
        UInt32 keycode;
        UInt32 modifier;
        UniChar unicode;
        char code;
        
        shouldCall = FALSE;
        
        switch (GetEventClass(inEvent))
        {
            case kEventClassKeyboard :
                switch (GetEventKind(inEvent))
            {
                case kEventRawKeyRepeat:
                case kEventRawKeyDown:
                    GetEventParameter(inEvent, kEventParamKeyCode, typeUInt32, NULL, sizeof(UInt32), NULL, &keycode);
                    GetEventParameter(inEvent, kEventParamKeyModifiers, typeUInt32, NULL, sizeof(UInt32), NULL, &modifier);
                    GetEventParameter(inEvent, kEventParamKeyUnicodes, typeUnicodeText, NULL, sizeof(UniChar), NULL, &unicode);
                    GetEventParameter(inEvent, kEventParamKeyMacCharCodes, typeChar, NULL, sizeof(char), NULL, &code);
                {
                    if(unicode == stopcode)
                    {
                        shouldCall = YES;
                        break;
                    }
                    
                    if((unicode >= 0xE000) && (unicode <= 0xF8FF)) break;
                    
                    if(buffer.size() == WAIT_BUFFER) break;
                    
                    buffer += unicode;
                }
                    break;
            }
                break;
        }
        
        if((shouldCall)&& (!isDying))
        {
            PA_NewProcess((void *)call, 0, (PA_Unichar *)"$\0v\0v\0\0\0");
        }
        
        return eventNotHandledErr;
    }
#endif
#else
    LRESULT CALLBACK onEventCall(int code, WPARAM wParam, LPARAM lParam)
    {
        shouldCall = FALSE;

        if (code == HC_ACTION)
        {
            MSG* pMsg = (MSG*)lParam;
            if (pMsg->message == WM_KEYDOWN)
            {
                UINT keycode = pMsg->wParam;
                BYTE keyboard[256];
                if (GetKeyboardState(keyboard))
                {
                    WCHAR buf[5] = {};
                    if (1 == ToUnicode(keycode, MapVirtualKey(keycode, MAPVK_VK_TO_VSC), keyboard, buf, 4, 0))
                    {
                        PA_Unichar unicode = buf[0];

                        if (buffer.size() == WAIT_BUFFER) buffer.clear();

                        if (unicode == stopcode)
                        {
                            shouldCall = TRUE;
                        }
                        else
                        {
                            buffer += unicode;
                        }

                    }

                }

            }
        }

        if((shouldCall) && (!isDying))
        {
            PA_NewProcess((void *)call, 0, (PA_Unichar *)"$\0v\0v\0\0\0");
        }
        
        return CallNextHookEx( eventMonitor, code, wParam, lParam );
    }
#endif
    
    void start()
    {
        buffer.clear();
        shouldCall = FALSE;
        
        if(!eventHookIsActive)
        {
#if VERSIONMAC
#if USE_NSEVENT
            eventMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSKeyDownMask
                                                                                                                        handler:^(NSEvent *event)
                                            {
                                                //NSString to CUTF16String
                                                C_TEXT buf;
                                                buf.setUTF16String([event charactersIgnoringModifiers]);
                                                CUTF16String u16;
                                                buf.copyUTF16String(&u16);
                                                shouldCall = FALSE;
                                                
                                                for(NSUInteger i = 0; i < u16.size();++i)
                                                {
                                                    PA_Unichar code = u16.at(i);
                                                
                                                    if(code == stopcode)
                                                    {
                                                        shouldCall = YES;
                                                        break;
                                                    }
                                                    
                                                    if((code >= 0xE000) && (code <= 0xF8FF)) break;
                                                    
                                                    if(buffer.size() == WAIT_BUFFER) break;
                                                    
                                                    buffer += code;
                                                }
                                                
                                                if((shouldCall)&& (!isDying))
                                                {
                                                    PA_NewProcess((void *)call, 0, (PA_Unichar *)"$\0v\0v\0\0\0");
                                                }
                                                
                                                return event;
                                            }];
#else
            EventTypeSpec kKeyboardEvents[] = {
                {kEventClassKeyboard, kEventRawKeyDown},
                {kEventClassKeyboard, kEventRawKeyRepeat}};
            EventHandlerUPP upp = NewEventHandlerUPP(onEventCall);
            InstallEventHandler(GetApplicationEventTarget(),
                                                    upp,
                                                    GetEventTypeCount(kKeyboardEvents),
                                                    kKeyboardEvents,
                                                    0, &eventMonitor);
            DisposeEventHandlerUPP(upp);
#endif
#else
            eventMonitor = SetWindowsHookEx(WH_GETMESSAGE, (HOOKPROC)onEventCall, 0, GetCurrentThreadId());
#endif
            if(eventMonitor)
            {
                eventHookIsActive = TRUE;
            }
        }
    }
    
    void stop()
    {
        if(eventHookIsActive)
        {
            if(eventMonitor)
            {
#if VERSIONMAC
#if USE_NSEVENT
                [NSEvent removeMonitor:eventMonitor];
#else
                RemoveEventHandler(eventMonitor);
#endif
#else
                UnhookWindowsHookEx(eventMonitor);
#endif
                eventMonitor = NULL;
            }
            buffer.clear();
            
            eventHookIsActive = FALSE;
            
        }
        
    }
    
    void setup( C_TEXT &Param1_stopcode,
                            C_TEXT &Param2_callback_method,
                            C_TEXT &Param3_callback_worker,
                            C_LONGINT &Param4_callback_window)
    {
        //stopcode
        CUTF16String u16;
        Param1_stopcode.copyUTF16String(&u16);
        stopcode = u16.size() ? u16.at(0) : 0;
        
        //actionMethod
        Param2_callback_method.copyUTF16String(&actionMethod);

        //actionWorker
        Param3_callback_worker.copyUTF16String(&actionWorker);
        
        //actionWindow
        actionWindow = (PA_long32)Param4_callback_window.getIntValue();
        
        if(stopcode)
        {
#if VERSIONMAC
            PA_RunInMainProcess((PA_RunInMainProcessProcPtr)start, NULL);
#else
            start();
#endif
            
        }else
        {
#if VERSIONMAC
            PA_RunInMainProcess((PA_RunInMainProcessProcPtr)stop, NULL);
#else
            Wait::stop();
#endif
        }
    }
}

#pragma mark Startup / Exit

#define MAX_PROCESS_NAME 256

bool IsProcessOnExit()
{
    std::vector<PA_Unichar> name(MAX_PROCESS_NAME);
    
    PA_long32 state, time;
    
    PA_GetProcessInfo(PA_GetCurrentProcessNumber(), &name[0], &state, &time);
    CUTF16String procName(&name[0]);
    CUTF16String exitProcName((PA_Unichar *)"$\0x\0x\0\0\0");
    return (!procName.compare(exitProcName));
}

void OnExit()
{
    Wait::isDying = TRUE;
#if VERSIONMAC
    PA_RunInMainProcess((PA_RunInMainProcessProcPtr)Wait::stop, NULL);
#else
#endif
}

void OnCloseProcess()
{
    if(IsProcessOnExit())
    {
        OnExit();
    }
}

#pragma mark -

void PluginMain(PA_long32 selector, PA_PluginParameters params) {
    
	try
	{
        switch(selector)
        {
            case kCloseProcess :
                OnCloseProcess();
                break;
                
			// --- GET TEXT INPUT
            
			case 1 :
                PA_RunInMainProcess((PA_RunInMainProcessProcPtr)GET_TEXT_INPUT, params);
				break;

        }

	}
	catch(...)
	{

	}
}

#pragma mark -

void GET_TEXT_INPUT(PA_PluginParameters params) {

//    sLONG_PTR *pResult = (sLONG_PTR *)params->fResult;
    PackagePtr pParams = (PackagePtr)params->fParameters;
    
    C_TEXT Param1_stopcode;
    C_TEXT Param2_callback_method;
    C_TEXT Param3_callback_worker;
    C_LONGINT Param4_callback_window;

    Param1_stopcode.fromParamAtIndex(pParams, 1);
    Param2_callback_method.fromParamAtIndex(pParams, 2);
    Param3_callback_worker.fromParamAtIndex(pParams, 3);
    Param4_callback_window.fromParamAtIndex(pParams, 4);

    Wait::setup(Param1_stopcode,
                            Param2_callback_method,
                            Param3_callback_worker,
                            Param4_callback_window);
}

