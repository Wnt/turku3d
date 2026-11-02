// Analytic atmosphere adapted from three.js SkyMesh (MIT, copyright 2010–2026 three.js authors).
// See analyticSky.LICENSE. The single graph exposes diffuse and solar radiance before cloud composition.
import * as THREE from 'three/webgpu';
import { Fn, float, vec3, acos, add, mul, clamp, cos, dot, exp, max, min, mix,
  modelViewProjection, normalize, positionWorld, pow, sub, varyingProperty, vec4, uniform, cameraPosition } from 'three/tsl';

type ComposeRadiance = (diffuse: THREE.Node<'vec3'>, solar: THREE.Node<'vec3'>) => THREE.Node<'vec4'>;

export class AnalyticSky extends THREE.Mesh<THREE.BoxGeometry, THREE.NodeMaterial> {
  readonly isSkyMesh = true;
  readonly turbidity = uniform(2);
  readonly rayleigh = uniform(1);
  readonly mieCoefficient = uniform(0.005);
  readonly mieDirectionalG = uniform(0.8);
  readonly sunPosition = uniform(new THREE.Vector3());
  readonly showSunDisc = uniform(1);
  private readonly vSunDirection = varyingProperty('vec3');
  private readonly vSunE = varyingProperty('float');
  private readonly vBetaR = varyingProperty('vec3');
  private readonly vBetaM = varyingProperty('vec3');

  constructor() {
    const material = new THREE.NodeMaterial();
    super(new THREE.BoxGeometry(1, 1, 1), material);
    const { vSunDirection, vSunE, vBetaR, vBetaM } = this;
    material.vertexNode = Fn(() => {
      const e = float( 2.718281828459045 );

      const totalRayleigh = vec3( 5.804542996261093E-6, 1.3562911419845635E-5, 3.0265902468824876E-5 );

      const MieConst = vec3( 1.8399918514433978E14, 2.7798023919660528E14, 4.0790479543861094E14 );

      const cutoffAngle = float( 1.6110731556870734 );
      const steepness = float( 1.5 );
      const EE = float( 1000.0 );

      const sunDirection = normalize( this.sunPosition );
      vSunDirection.assign( sunDirection );

      const angle = sunDirection.y;
      const zenithAngleCos = clamp( angle, - 1, 1 );
      const sunIntensity = EE.mul( max( 0.0, float( 1.0 ).sub( pow( e, cutoffAngle.sub( acos( zenithAngleCos ) ).div( steepness ).negate() ) ) ) );
      vSunE.assign( sunIntensity );

      const sunfade = float( 1.0 ).sub( clamp( float( 1.0 ).sub( exp( this.sunPosition.y.div( 450000.0 ) ) ), 0, 1 ) );

      const rayleighCoefficient = this.rayleigh.sub( float( 1.0 ).mul( float( 1.0 ).sub( sunfade ) ) );

      vBetaR.assign( totalRayleigh.mul( rayleighCoefficient ) );

      const c = float( 0.2 ).mul( this.turbidity ).mul( 10E-18 );
      const totalMie = float( 0.434 ).mul( c ).mul( MieConst );

      vBetaM.assign( totalMie.mul( this.mieCoefficient ) );

      const position = modelViewProjection as THREE.Node<'vec4'>;
      position.z.assign( position.w ); // set z to camera.far

      return position;
    })();
    material.side = THREE.BackSide;
    material.depthWrite = false;
    material.fog = false;
    material.colorNode = this.createRadianceNode((diffuse, solar) => vec4(diffuse.add(solar), 1));
  }

  /** Evaluate scattering once, then let the atmosphere independently obscure the direct disc. */
  createRadianceNode(compose: ComposeRadiance): THREE.Node<'vec4'> {
    const { vSunDirection, vSunE, vBetaR, vBetaM } = this;
    return Fn(() => {
      const pi = float( 3.141592653589793 );

      const rayleighZenithLength = float( 8.4E3 );
      const mieZenithLength = float( 1.25E3 );
      const sunAngularDiameterCos = float( 0.9999566769464484 );

      const THREE_OVER_SIXTEENPI = float( 0.05968310365946075 );
      const ONE_OVER_FOURPI = float( 0.07957747154594767 );

      const direction = normalize( positionWorld.sub( cameraPosition ) );

      const zenithAngle = acos( max( 0.0, direction.y ) );
      const inverse = float( 1.0 ).div( cos( zenithAngle ).add( float( 0.15 ).mul( pow( float( 93.885 ).sub( zenithAngle.mul( 180.0 ).div( pi ) ), - 1.253 ) ) ) );
      const sR = rayleighZenithLength.mul( inverse );
      const sM = mieZenithLength.mul( inverse );

      const Fex = exp( mul( vBetaR, sR ).add( mul( vBetaM, sM ) ).negate() );

      const cosTheta = dot( direction, vSunDirection );

      const c = cosTheta.mul( 0.5 ).add( 0.5 );
      const rPhase = THREE_OVER_SIXTEENPI.mul( float( 1.0 ).add( pow( c, 2.0 ) ) );
      const betaRTheta = vBetaR.mul( rPhase );

      const g2 = pow( this.mieDirectionalG, 2.0 );
      const inv = float( 1.0 ).div( pow( float( 1.0 ).sub( float( 2.0 ).mul( this.mieDirectionalG ).mul( cosTheta ) ).add( g2 ), 1.5 ) );
      const mPhase = ONE_OVER_FOURPI.mul( float( 1.0 ).sub( g2 ) ).mul( inv );
      const betaMTheta = vBetaM.mul( mPhase );

      const Lin = pow( vSunE.mul( add( betaRTheta, betaMTheta ).div( add( vBetaR, vBetaM ) ) ).mul( sub( 1.0, Fex ) ), vec3( 1.5 ) );
      Lin.mulAssign( mix( vec3( 1.0 ), pow( vSunE.mul( add( betaRTheta, betaMTheta ).div( add( vBetaR, vBetaM ) ) ).mul( Fex ), vec3( 1.0 / 2.0 ) ), clamp( pow( sub( 1.0, vSunDirection.y ), 5.0 ), 0.0, 1.0 ) ) );

      const L0 = vec3( 0.1 ).mul( Fex );

      const sundisc = clamp( cosTheta.sub( sunAngularDiameterCos ).mul( 50000.0 ), 0.0, 1.0 ).mul( this.showSunDisc );
      const sundiscColor = min( vSunE.mul( Fex ), 80.0 ).mul( 760.0 ).mul( sundisc );
      const diffuse = add(Lin, L0).mul(0.04).add(vec3(0, 0.0003, 0.00075));
      return compose(diffuse, sundiscColor);
    })();
  }
}
