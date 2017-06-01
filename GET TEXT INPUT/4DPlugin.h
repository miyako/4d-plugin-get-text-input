/* --------------------------------------------------------------------------------
 #
 #	4DPlugin.h
 #	source generated by 4D Plugin Wizard
 #	Project : GET TEXT INPUT
 #	author : miyako
 #	2017/05/31
 #
 # --------------------------------------------------------------------------------*/



// --- GET TEXT INPUT
void GET_TEXT_INPUT(sLONG_PTR *pResult, PackagePtr pParams);

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
