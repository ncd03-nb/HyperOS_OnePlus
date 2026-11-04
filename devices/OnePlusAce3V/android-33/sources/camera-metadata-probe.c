#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc,char**argv){
 if(argc<2)return 2;
 void*h=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
 if(!h){fprintf(stderr,"dlopen: %s\n",dlerror());return 3;}
 int(*type)(uint32_t)=dlsym(h,"get_camera_metadata_tag_type");
 const char*(*name)(uint32_t)=dlsym(h,"get_camera_metadata_tag_name");
 if(!type||!name)return 4;
 if(argc==2){
  for(uint32_t section=0;section<40;section++)for(uint32_t index=0;index<256;index++){
   uint32_t tag=(section<<16)|index;int t=type(tag);
   if(t>=0)printf("TAG %08x %d %s\n",tag,t,name(tag));
  }
 }else{
  uint32_t tag=strtoul(argv[2],0,0);int t=type(tag);
  void*(*alloc)(size_t,size_t)=dlsym(h,"allocate_camera_metadata");
  int(*add)(void*,uint32_t,const void*,size_t)=dlsym(h,"add_camera_metadata_entry");
  int(*validate)(const void*,const size_t*)=dlsym(h,"validate_camera_metadata_structure");
  void(*release)(void*)=dlsym(h,"free_camera_metadata");
  if(!alloc||!add||!validate||!release)return 5;
  if(t<0){printf("TAG_UNSUPPORTED %08x\n",tag);return 0;}
  void*m=alloc(1,64);if(!m)return 6;
  uint64_t data[2]={0,0};int a=add(m,tag,data,1),v=validate(m,0);
  printf("TAG_VALIDATION %08x type=%d add=%d validate=%d\n",tag,t,a,v);
  release(m);if(a||v)return 7;
 }
 dlclose(h);return 0;
}
