// Reusable parts described as data for parameterised Blenderwright MCP tools.
// Axle is local X; object origin is the hub. No Blender Python is generated.
export function latheGraph(profile, segments=48) {
  const n=profile.length, nodes=[],links=[];
  const node=(name,type,inputs={},settings={})=>nodes.push({name,type,inputs,settings});
  const link=(...a)=>links.push(a);
  const math=(name,operation,inputs={})=>node(name,'ShaderNodeMath',inputs,{operation});
  node('grid','GeometryNodeMeshGrid',{'Vertices X':segments+1,'Vertices Y':n});
  node('index','GeometryNodeInputIndex');math('p','MODULO',{1:n});math('a0','DIVIDE',{1:n});math('a1','FLOOR');math('angle','MULTIPLY',{1:Math.PI*2/segments});
  link('index','Index','p',0);link('index','Index','a0',0);link('a0',0,'a1',0);link('a1',0,'angle',0);
  profile.forEach(([x,r],i)=>{math(`eq${i}`,'COMPARE',{1:i,2:.1});node(`p${i}`,'GeometryNodeSwitch',{True:[x,r,0],False:[0,0,0]},{input_type:'VECTOR'});link('p',0,`eq${i}`,0);link(`eq${i}`,0,`p${i}`,'Switch');if(i)link(`p${i-1}`,'Output',`p${i}`,'False');});
  node('section','ShaderNodeSeparateXYZ');link(`p${n-1}`,'Output','section','Vector');
  for(const [axis,operation]of [['y','COSINE'],['z','SINE']]){math(axis,operation);math(`${axis}r`,'MULTIPLY');link('angle',0,axis,0);link(axis,0,`${axis}r`,0);link('section','Y',`${axis}r`,1);}
  node('position','ShaderNodeCombineXYZ');link('section','X','position','X');link('yr',0,'position','Y');link('zr',0,'position','Z');
  node('fit','GeometryNodeSetPosition');link('grid','Mesh','fit','Geometry');link('position','Vector','fit','Position');link('fit','Geometry','Group Output','Geometry');
  return {nodes,links};
}

export function wheelRecipe({name,center,radius=.32985,width=.225,side=1,segments=48,spokes=10}) {
  const ops=[],parts=[],add=(tool,args)=>ops.push({tool,args});
  const lathe=(suffix,profile,material)=>{const part=`${name}_${suffix}`;parts.push(part);
    add('create_object',{type:'PLANE',name:part,location:center});add('build_geometry_nodes',{object_name:part,modifier_name:'Wheel section',spec:latheGraph(profile,segments)});
    add('convert_object',{object_name:part,target:'MESH'});add('merge_vertices',{object_name:part,threshold:.0001});add('recalculate_normals',{object_name:part,inside:false});add('assign_material',{object_name:part,material_name:material});add('set_smooth_shading',{object_name:part,smooth:true});};
  const w=width/2,r=radius,rim=r*.693;
  lathe('tyre',[[-w*.88,rim],[-w,r*.78],[-w,r*.91],[-w*.84,r*.98],[-w*.65,r],[w*.65,r],[w*.84,r*.98],[w,r*.91],[w,r*.78],[w*.88,rim],[-w*.88,rim]],'fleet_rubber');
  lathe('rim',[[-w*.83,rim*.97],[-w*.83,rim*.92],[w*.88,rim*.92],[w*.96,rim*.95],[w*.96,rim],[w*.88,rim*1.025],[-w*.83,rim*1.025],[-w*.83,rim*.97]],'fleet_alloy');
  lathe('disc',[[side*w*.35,0],[side*w*.35,rim*.78],[side*w*.40,rim*.78],[side*w*.40,0]],'fleet_brake_disc');
  lathe('hub',[[side*w*.45,0],[side*w*.45,rim*.23],[side*w*.91,rim*.23],[side*w*.95,0]],'fleet_alloy');
  // Split spokes leave deep openings through to the brake disc.
  for(let i=0;i<spokes;i++)for(const fork of [-1,1]){
    const a=i*2*Math.PI/spokes+fork*.045,rad=rim*.59,len=rim*.36;
    const part=`${name}_spoke_${i}_${fork}`;parts.push(part);
    add('create_object',{type:'CUBE',name:part,location:[center[0]+side*w*.77,center[1]+Math.cos(a)*rad,center[2]+Math.sin(a)*rad],rotation:[a-Math.PI/2,0,0],scale:[.013,rim*.022,len]});
    add('apply_transforms',{object_name:part,location:false,rotation:false,scale:true});add('assign_material',{object_name:part,material_name:'fleet_alloy'});
    add('bevel_edges',{object_name:part,width:.002,segments:1});
  }
  add('join_objects',{names:parts});add('rename_object',{old_name:parts[0],new_name:name});
  add('move_to_collection',{object_names:[name],collection_name:'PARTS'});
  return ops;
}
