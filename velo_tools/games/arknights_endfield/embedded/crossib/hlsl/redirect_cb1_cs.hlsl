StructuredBuffer<uint4> DumpedCB1  : register(t0);
Buffer<float> TargetPartID         : register(t2); 

RWStructuredBuffer<uint4> FakeCB1_UAV : register(u0); 

[numthreads(1024, 1, 1)] 
void main(uint3 tid : SV_DispatchThreadID) {
    uint id = tid.x;
    if (id >= 4096) return; 

    uint4 cb_data = DumpedCB1[id];
    
    if (id == 5) {
        uint target_offset = (uint)TargetPartID[0]; 
        
        cb_data.x = target_offset;               
        cb_data.y = target_offset + 100000;      
    }
    FakeCB1_UAV[id] = cb_data; 
}
