/* --------------------------------------------------------------------------------
 #
 #	4DPlugin-GET-TEXT-INPUT.h
 #	source generated by 4D Plugin Wizard
 #	Project : GET TEXT INPUT
 #	author : miyako
 #	2024/07/03
 #  
 # --------------------------------------------------------------------------------*/

#ifndef PLUGIN_GET_TEXT_INPUT_H
#define PLUGIN_GET_TEXT_INPUT_H

#include "4DPluginAPI.h"
#include "C_TEXT.h"
#include "C_LONGINT.h"

#pragma mark -

void GET_TEXT_INPUT(PA_PluginParameters params);

#if VERSIONMAC
#if CGFLOAT_IS_DOUBLE
#define USE_NSEVENT 1
#else
#define USE_NSEVENT 0
#endif
#endif

#define WAIT_BUFFER 4096

namespace Wait
{
#if VERSIONMAC
#if USE_NSEVENT
    typedef id HHOOK;
#else
    typedef EventHandlerRef HHOOK;
#endif
#endif
}

#endif /* PLUGIN_GET_TEXT_INPUT_H */