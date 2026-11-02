// Pure data for Blenderwright build_geometry_nodes. All Blender operations run through MCP.
// A longitudinal quad loft: station=[y, halfWidth, height], profile=[xFraction,zOffset,heightWeight].
export function loftGraph(stations, profile, { frontSweep=0, rearSweep=0, frontLift=0, rearLift=0, frontShoulderRise=0 }={}) {
  const nodes=[], links=[], nx=profile.length, ny=stations.length;
  const node=(name,type,inputs={},settings={})=>nodes.push({name,type,inputs,settings});
  const link=(...edge)=>links.push(edge);
  const math=(name,operation,inputs={})=>node(name,'ShaderNodeMath',inputs,{operation});
  const lookup=(name,values,index)=>{
    values.forEach((value,i)=>{
      math(`${name}_eq_${i}`,'COMPARE',{1:i,2:.1});
      node(`${name}_${i}`,'GeometryNodeSwitch',{True:value,False:[0,0,0]},{input_type:'VECTOR'});
      link(index,0,`${name}_eq_${i}`,0);link(`${name}_eq_${i}`,0,`${name}_${i}`,'Switch');
      if(i)link(`${name}_${i-1}`,'Output',`${name}_${i}`,'False');
    });
    return `${name}_${values.length-1}`;
  };
  // Blender Grid indexes Y first; cross-section controls must be the fast axis.
  node('grid','GeometryNodeMeshGrid',{'Vertices X':ny,'Vertices Y':nx});node('index','GeometryNodeInputIndex');
  math('u','MODULO',{1:nx});math('div','DIVIDE',{1:nx});math('v','FLOOR');
  link('index','Index','u',0);link('index','Index','div',0);link('div',0,'v',0);
  const p=lookup('profile',profile,'u'),s=lookup('station',stations,'v');
  node('p','ShaderNodeSeparateXYZ');node('s','ShaderNodeSeparateXYZ');
  link(p,'Output','p','Vector');link(s,'Output','s','Vector');
  math('x','MULTIPLY');link('p','X','x',0);link('s','Y','x',1);
  math('z_height','MULTIPLY');link('p','Z','z_height',0);link('s','Z','z_height',1);
  math('z','ADD');link('z_height',0,'z',0);link('p','Y','z',1);
  math('front','SUBTRACT',{0:3});link('v',0,'front',1);
  math('front_clamp','MAXIMUM',{1:0});link('front',0,'front_clamp',0);
  math('front_bulge','MULTIPLY',{1:frontSweep/3});link('front_clamp',0,'front_bulge',0);
  math('rear','SUBTRACT',{1:ny-3});link('v',0,'rear',0);
  math('rear_clamp','MAXIMUM',{1:0});link('rear',0,'rear_clamp',0);
  math('rear_bulge','MULTIPLY',{1:rearSweep/2});link('rear_clamp',0,'rear_bulge',0);
  math('end_bulge','SUBTRACT');link('front_bulge',0,'end_bulge',0);link('rear_bulge',0,'end_bulge',1);
  math('side_squared','MULTIPLY');link('p','X','side_squared',0);link('p','X','side_squared',1);
  math('y_sweep','MULTIPLY');link('side_squared',0,'y_sweep',0);link('end_bulge',0,'y_sweep',1);
  math('y','ADD');link('s','X','y',0);link('y_sweep',0,'y',1);
  math('lift_front','MULTIPLY',{1:frontLift/3});link('front_clamp',0,'lift_front',0);
  math('lift_rear','MULTIPLY',{1:rearLift/2});link('rear_clamp',0,'lift_rear',0);
  math('lift_total','ADD');link('lift_front',0,'lift_total',0);link('lift_rear',0,'lift_total',1);
  math('low_weight','SUBTRACT',{0:.9});link('z',0,'low_weight',1);
  math('low_clamp','MAXIMUM',{1:0});link('low_weight',0,'low_clamp',0);
  math('low_scaled','DIVIDE',{1:.9});link('low_clamp',0,'low_scaled',0);
  math('lift','MULTIPLY');link('low_scaled',0,'lift',0);link('lift_total',0,'lift',1);
  math('lifted_z','ADD');link('lift',0,'lifted_z',0);link('z',0,'lifted_z',1);
  math('wing_range','SUBTRACT',{0:5});link('v',0,'wing_range',1);
  math('wing_clamp','MAXIMUM',{1:0});link('wing_range',0,'wing_clamp',0);
  math('wing_height','MULTIPLY',{1:frontShoulderRise/5});link('wing_clamp',0,'wing_height',0);
  math('wing_width','MULTIPLY');link('wing_height',0,'wing_width',0);link('side_squared',0,'wing_width',1);
  math('wing_weight','MULTIPLY');link('wing_width',0,'wing_weight',0);link('p','Z','wing_weight',1);
  math('final_z','ADD');link('lifted_z',0,'final_z',0);link('wing_weight',0,'final_z',1);
  node('position','ShaderNodeCombineXYZ');for(const axis of ['x','y'])link(axis,0,'position',axis.toUpperCase());link('final_z',0,'position','Z');
  node('fit','GeometryNodeSetPosition');link('grid','Mesh','fit','Geometry');
  link('position','Vector','fit','Position');link('fit','Geometry','Group Output','Geometry');
  if(nodes.length>200)throw Error(`Loft graph exceeds Blenderwright's 200-node limit: ${nodes.length}`);
  return {nodes,links};
}

export function loftRecipe({name,stations,profile,material='fleet_paint',collection='CAGE',subdivision=2,regions={},...shape}) {
  const nx=profile.length,ny=stations.length,operations=[];
  const add=(tool,args)=>operations.push({tool,args});
  add('create_object',{type:'PLANE',name});
  add('build_geometry_nodes',{object_name:name,modifier_name:'Measured quad cage',spec:loftGraph(stations,profile,shape)});
  add('convert_object',{object_name:name,target:'MESH'});
  for(const [group_name,rows]of Object.entries(regions))add('assign_vertex_group',{object_name:name,group_name,vertex_indices:rows.flatMap(j=>Array.from({length:nx},(_,i)=>j*nx+i))});
  add('assign_material',{object_name:name,material_name:material});
  add('add_modifier',{object_name:name,modifier_type:'SUBSURF',name:'Body curvature'});
  for(const property of ['levels','render_levels'])add('set_modifier_property',{object_name:name,modifier_name:'Body curvature',property,value:subdivision});
  add('set_smooth_shading',{object_name:name,smooth:true});
  add('move_to_collection',{object_names:[name],collection_name:collection});
  // Explicit quad caps share the perimeter controls. Do not infer post-weld vertex indices.
  const caps=[],half=profile.slice(0,(nx+1)/2);
  for(const end of [0,1]) {
    const [y,width,height]=stations[end?ny-1:0],points=[];
    const sweep=end?-(shape.rearSweep??0):(shape.frontSweep??0),lift=end?(shape.rearLift??0):(shape.frontLift??0);
    for(const sign of [-1,0,1])for(const [fraction,zOffset,heightWeight]of half) {
      const x=sign*fraction,z=zOffset+heightWeight*height;
      points.push([x*width,y+x*x*sweep,z+lift*Math.max(0,(.9-z)/.9)+(end?0:(shape.frontShoulderRise??0)*x*x*heightWeight)]);
    }
    const cap=`${name}_cap_${end}`;caps.push(cap);
    operations.push(...panelRecipe({name:cap,points,columns:3,rows:half.length,material}));
  }
  add('join_objects',{names:[name,...caps]});
  add('merge_vertices',{object_name:name,threshold:.0001});
  add('recalculate_normals',{object_name:name,inside:false});
  return operations;
}

/** Quad surface patches fitted to the same cage; reusable for glazing and fascia inserts. */
export function panelRecipe({name,points,columns,rows,material='fleet_glass',target,offset=.003,subdivide=0,projection}) {
  if(points.length!==columns*rows||points.length>90)throw Error('Invalid panel grid');
  const nodes=[{name:'grid',type:'GeometryNodeMeshGrid',inputs:{'Vertices X':columns,'Vertices Y':rows}},
    {name:'index',type:'GeometryNodeInputIndex'},{name:'fit',type:'GeometryNodeSetPosition'}];
  const links=[['grid','Mesh','fit','Geometry'],['fit','Geometry','Group Output','Geometry']];
  points.forEach((point,i)=>{
    nodes.push({name:`at_${i}`,type:'ShaderNodeMath',settings:{operation:'COMPARE'},inputs:{1:i,2:.1}},
      {name:`point_${i}`,type:'GeometryNodeSwitch',settings:{input_type:'VECTOR'},inputs:{True:point,False:[0,0,0]}});
    links.push(['index','Index',`at_${i}`,0],[`at_${i}`,0,`point_${i}`,'Switch']);
    if(i)links.push([`point_${i-1}`,'Output',`point_${i}`,'False']);
  });
  links.push([`point_${points.length-1}`,'Output','fit','Position']);
  const ops=[{tool:'create_object',args:{type:'PLANE',name,location:projection?.origin??[0,0,0]}},
    {tool:'build_geometry_nodes',args:{object_name:name,modifier_name:'Panel grid',spec:{nodes,links}}},
    {tool:'convert_object',args:{object_name:name,target:'MESH'}},
    {tool:'assign_material',args:{object_name:name,material_name:material}},
    {tool:'set_smooth_shading',args:{object_name:name,smooth:true}},
    {tool:'move_to_collection',args:{object_names:[name],collection_name:'PARTS'}}];
  if(subdivide)ops.push({tool:'subdivide_mesh',args:{object_name:name,cuts:subdivide}});
  if(target)ops.push({tool:'add_modifier',args:{object_name:name,modifier_type:'SHRINKWRAP',name:'Fit to cage'}},
    ...Object.entries({target,offset,...(projection?{wrap_method:'PROJECT',use_project_x:projection.axis==='X',use_project_y:projection.axis==='Y',use_project_z:projection.axis==='Z',use_positive_direction:projection.direction>0,use_negative_direction:projection.direction<0}:{})}).map(([property,value])=>({tool:'set_modifier_property',args:{object_name:name,modifier_name:'Fit to cage',property,value}})));
  return ops;
}
