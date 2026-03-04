/**
 * Surface Configuration
 * 
 * Tournament→Surface mapping, metric weight vectors, and OVR curve.
 * Single source of truth; consumed by compute_metrics.js.
 */

// ═══════════════════════════════════════════════════════════════
// Tournament → Surface Mapping
// ═══════════════════════════════════════════════════════════════
// Key = tournament name as extracted from match_id (3rd segment)
// Value = 'Hard' | 'Clay' | 'Grass'
//
// Missing tournaments default to 'Hard' (most common surface).

const TOURNAMENT_SURFACE = {
    // ── Grand Slams ──
    Australian_Open: 'Hard', Australian_Open_Juniors: 'Hard',
    Roland_Garros: 'Clay', Roland_Garros_Juniors: 'Clay',
    Wimbledon: 'Grass', Wimbedon: 'Grass', Wimbledon_Juniors: 'Grass',
    US_Open: 'Hard',

    // ── Masters 1000 ──
    Indian_Wells: 'Hard', Indian_Wells_Masters: 'Hard',
    Miami: 'Hard', Miami_Masters: 'Hard', Key_Biscayne: 'Hard',
    Monte_Carlo: 'Clay', Monte_Carlo_Masters: 'Clay',
    Madrid: 'Clay', Madrid_Masters: 'Clay',
    Rome: 'Clay', Rome_Masters: 'Clay',
    Canada_Masters: 'Hard',
    Cincinnati: 'Hard', Cincinnati_Masters: 'Hard',
    Shanghai_Masters: 'Hard',
    Paris: 'Hard', Paris_Masters: 'Hard',

    // ── ATP 500 ──
    Rotterdam: 'Hard',
    Dubai: 'Hard',
    Acapulco: 'Hard', Acapulco_CH: 'Hard',
    Barcelona: 'Clay', Barcelona_CH: 'Clay',
    Hamburg: 'Clay', Hamburg_CH: 'Clay', Hamburg_Masters: 'Clay',
    Halle: 'Grass',
    Queens_Club: 'Grass',
    Washington: 'Hard',
    Beijing: 'Hard',
    Tokyo: 'Hard', Tokyo_Indoor: 'Hard', Tokyo_Outdoor: 'Hard',
    Vienna: 'Hard',
    Basel: 'Hard',

    // ── ATP 250 Clay ──
    Buenos_Aires: 'Clay', Buenos_Aires_CH: 'Clay',
    Cordoba: 'Clay',
    Santiago: 'Clay', Santiago_CH: 'Clay',
    Marrakech: 'Clay',
    Houston: 'Clay', Houston_CH: 'Clay',
    Bucharest: 'Clay',
    Geneva: 'Clay',
    Lyon: 'Clay', Lyon_CH: 'Clay',
    Bastad: 'Clay',
    Gstaad: 'Clay',
    Kitzbuhel: 'Clay',
    Umag: 'Clay',
    Palermo: 'Clay',
    Sao_Paulo: 'Clay', Sao_Paulo_CH: 'Clay',
    Estoril: 'Clay',
    Nice: 'Clay',
    Casablanca: 'Clay',
    Dusseldorf: 'Clay',
    Florence: 'Clay', Florence_CH: 'Clay',
    Munich: 'Clay',
    Naples: 'Clay', Naples_CH: 'Clay', Napoli_CH: 'Clay',
    Quito: 'Clay',
    Rio_de_Janeiro: 'Clay',

    // ── ATP 250 Grass ──
    Eastbourne: 'Grass',
    Nottingham: 'Grass', Nottingham_CH: 'Grass',
    s_Hertogenbosch: 'Grass',
    Mallorca: 'Grass',
    Newport: 'Grass',
    Stuttgart: 'Grass', Stuttgart_Outdoor: 'Grass',

    // ── ATP 250 Hard ──
    Adelaide: 'Hard',
    Auckland: 'Hard',
    Brisbane: 'Hard',
    Doha: 'Hard', Doha_EXH: 'Hard',
    Pune: 'Hard', Pune_CH: 'Hard',
    Montpellier: 'Hard',
    Marseille: 'Hard',
    Dallas: 'Hard', Dallas_CH: 'Hard', Dallas_WCT: 'Hard',
    Delray_Beach: 'Hard',
    Los_Cabos: 'Hard',
    Atlanta: 'Hard',
    Winston_Salem: 'Hard',
    Metz: 'Hard',
    Chengdu: 'Hard',
    Zhuhai: 'Hard', Zhuhai_CH: 'Hard',
    Antwerp: 'Hard',
    Stockholm: 'Hard', Stockholm_Masters: 'Hard',
    St_Petersburg: 'Hard',
    Moscow: 'Hard',
    Sofia: 'Hard',
    Singapore: 'Hard',
    Chennai: 'Hard', Chennai_CH: 'Hard',
    Memphis: 'Hard',
    Sydney: 'Hard', Sydney_CH: 'Hard', Sydney_Indoor: 'Hard',
    San_Jose: 'Hard',
    Los_Angeles: 'Hard',
    Long_Island: 'Hard',
    New_Haven: 'Hard',
    Bangkok: 'Hard', Bangkok_CH: 'Hard',
    Kuala_Lumpur: 'Hard',
    Hong_Kong: 'Hard',
    Valencia: 'Hard', Valencia_CH: 'Hard',
    San_Diego: 'Hard',
    San_Francisco: 'Hard', San_Francisco_CH: 'Hard',
    Indianapolis: 'Hard', Indianapolis_CH: 'Hard',
    Melbourne: 'Hard',
    New_York: 'Hard',
    Orlando: 'Hard', Orlando_CH: 'Hard',
    Shenzhen: 'Hard', Shenzhen_CH: 'Hard',
    Cologne: 'Hard',
    Gijon: 'Hard',
    Seoul: 'Hard', Seoul_CH: 'Hard',
    Hangzhou: 'Hard',
    Tel_Aviv: 'Hard',
    Ostrava: 'Hard', Ostrava_CH: 'Hard',
    Las_Vegas: 'Hard',
    Philadelphia: 'Hard',
    Scottsdale: 'Hard',
    Milan: 'Hard',
    Almaty: 'Hard', Almaty_CH: 'Hard',
    Belgrade: 'Hard',
    Chicago: 'Hard', Chicago_CH: 'Hard',
    Astana: 'Hard', Astana_CH: 'Hard',
    Istanbul: 'Hard', Istanbul_CH: 'Hard',

    // ── Indoor Hard (historical) ──
    Stuttgart_Indoor: 'Hard', Stuttgart_Classic: 'Hard', Stuttgart_Masters: 'Hard',
    Wembley: 'Hard',
    Essen_Masters: 'Hard',
    Forest_Hills: 'Hard', Forest_Hills_WCT: 'Hard',
    Luedenscheid: 'Hard',
    Stratton_Mountain: 'Hard',
    Scheveningen: 'Hard', Scheveningen_CH: 'Hard',

    // ── Year-end events ──
    Tour_Finals: 'Hard',
    Masters: 'Hard', Masters_Cup: 'Hard',
    NextGen_Finals: 'Hard',
    Young_Masters: 'Hard',
    Laver_Cup: 'Hard',

    // ── Team events ──
    ATP_Cup: 'Hard',
    Davis_Cup_EUR_SF: 'Clay', // varies, defaulting common
    Davis_Cup_Finals: 'Hard',
    Davis_Cup_G2_R1: 'Clay',
    Davis_Cup_Group_I: 'Hard',
    Davis_Cup_Qualifiers: 'Hard',
    Davis_Cup_SF: 'Hard',
    Davis_Cup_WG_II: 'Hard',
    Davis_Cup_WG2: 'Hard',
    Davis_Cup_World_Group: 'Hard',
    Davis_Cup_World_Group_F: 'Hard',
    Davis_Cup_World_Group_PO: 'Hard',
    Davis_Cup_World_Group_QF: 'Hard',
    Davis_Cup_World_Group_R1: 'Hard',
    Davis_Cup_World_Group_SF: 'Hard',
    Hopman_Cup: 'Hard',
    United_Cup: 'Hard',

    // ── Olympics ──
    Olympics: 'Hard', Paris_Olympics: 'Clay', Tokyo_Olympics: 'Hard',

    // ── Exhibition ──
    Six_Kings_Slam: 'Hard',
    Kooyong_Classic: 'Hard',
    St_Anton_EXH: 'Hard',
    Grand_Slam_Cup: 'Hard',
    Suntory_Cup: 'Hard',
    WITC_Hilton_Head: 'Clay',
    Nike_Junior_Tour: 'Hard',
    Pepsi_Grand_Slam: 'Hard',
    French_Club: 'Clay',
    Great_Ocean_Road_Open: 'Hard',
    Dutch_Championships: 'Clay',

    // ── Challengers — Clay ──
    Aix_En_Provence_CH: 'Clay',
    Banja_Luka_CH: 'Clay',
    Barletta_CH: 'Clay',
    Bogota_CH: 'Clay', Bogota: 'Clay',
    Bordeaux_CH: 'Clay',
    Braunschweig_CH: 'Clay',
    Brescia_CH: 'Clay',
    Buenos_Aires_CH: 'Clay',
    Cali_CH: 'Clay',
    Campinas_CH: 'Clay',
    Cordenons_CH: 'Clay',
    Corrientes_CH: 'Clay',
    Curitiba_CH: 'Clay',
    Forli_CH: 'Clay',
    Girona_CH: 'Clay',
    Granby_CH: 'Clay',
    Lima_CH: 'Clay',
    Leon_CH: 'Clay',
    Maia_CH: 'Clay',
    Marbella_CH: 'Clay',
    Merida_CH: 'Clay',
    Monterrey_CH: 'Clay',
    Montevideo_CH: 'Clay',
    Murcia_CH: 'Clay',
    Oeiras_CH: 'Clay', Oeiras_1_CH: 'Clay', Oeiras_3_CH: 'Clay',
    Pereira_CH: 'Clay',
    Perugia_CH: 'Clay',
    Porto_Alegre_CH: 'Clay',
    Porto_CH: 'Clay',
    Poznan_CH: 'Clay',
    Prostejov_CH: 'Clay',
    Puebla_CH: 'Clay',
    Punta_del_Este_CH: 'Clay',
    Rome_CH: 'Clay', Rome_GA_CH: 'Clay',
    San_Benedetto_del_Tronto_CH: 'Clay',
    San_Luis_Potosi_CH: 'Clay',
    Sao_Leopoldo_CH: 'Clay',
    Sassuolo_CH: 'Clay',
    Segovia_CH: 'Clay',
    Sophia_Antipolis_CH: 'Clay',
    Sopot_CH: 'Clay',
    Split_CH: 'Clay',
    Temuco_CH: 'Clay',
    Tenerife_CH: 'Clay',
    Tigre_CH: 'Clay',
    Verona_CH: 'Clay',
    Vicenza_CH: 'Clay',
    Villa_Maria_CH: 'Clay',
    Villena_CH: 'Clay',
    Andria_CH: 'Clay',
    Bergamo_CH: 'Clay',
    Biella_CH: 'Clay',
    Cagliari_CH: 'Clay',
    Cap_Cana_CH: 'Clay',
    Ercolano_CH: 'Clay',
    Guadalajara_CH: 'Clay',
    Guayaquil_CH: 'Clay',
    Hersonissos_CH: 'Clay', Hersonnisos_CH: 'Clay',
    Lugano_CH: 'Clay',
    Manerbio_CH: 'Clay',
    Montemar_CH: 'Clay',
    Ortisei_CH: 'Clay',
    Padova_CH: 'Clay',
    Pau_CH: 'Clay',
    Puerto_Vallarta_CH: 'Clay',
    Rennes_CH: 'Clay',
    Roanne_CH: 'Clay',
    Samarkand_CH: 'Clay',
    Santo_Domingo_CH: 'Clay',
    Savannah_CH: 'Clay',
    Sibiu_CH: 'Clay',
    Szczecin_CH: 'Clay',
    Szekesfehervar_CH: 'Clay',
    Tulln_CH: 'Clay',
    Wroclaw_CH: 'Clay',
    Zadar_CH: 'Clay',
    Zagreb_CH: 'Clay', Zagreb: 'Clay',
    Antofagasta_CH: 'Clay',
    Asuncion_CH: 'Clay',
    Brazzaville_CH: 'Clay',
    Dobrich_CH: 'Clay',
    Panama_City_CH: 'Clay',
    Saint_Tropez_CH: 'Clay',
    Anning_CH: 'Clay',

    // ── Challengers — Grass ──
    Ilkey_CH: 'Grass', Ilkley_CH: 'Grass',
    Surbiton_CH: 'Grass',
    Newport_Beach_CH: 'Hard', // actually hard

    // ── Challengers — Hard ──
    Alphen_Aan_Den_Rijn_CH: 'Hard',
    Amersfoort_CH: 'Hard', Amersfoort: 'Hard', Amsterdam: 'Hard',
    Athens: 'Hard',
    Bloomfield_Hills_CH: 'Hard',
    Blois_CH: 'Hard',
    Bratislava_CH: 'Hard', Bratislava_2_CH: 'Hard',
    Brest_CH: 'Hard',
    Brussels: 'Hard',
    Budapest: 'Hard', Antalya: 'Hard',
    Burnie_CH: 'Hard',
    Busan_CH: 'Hard',
    Canberra_CH: 'Hard',
    Champaign_CH: 'Hard',
    Charlottesville_CH: 'Hard',
    Cherbourg_CH: 'Hard',
    Cleveland_CH: 'Hard',
    Columbus_CH: 'Hard',
    Drummondville_CH: 'Hard',
    Eckental_CH: 'Hard',
    Fairfield_CH: 'Hard',
    Fergana_CH: 'Hard',
    Guangzhou_CH: 'Hard',
    Gwangju_CH: 'Hard',
    Hagen_CH: 'Hard',
    Helsinki_CH: 'Hard',
    Ho_Chi_Minh_City_CH: 'Hard',
    Ismaning_CH: 'Hard',
    Izmir_CH: 'Hard',
    Kaohsiung_CH: 'Hard',
    Karlsruhe_CH: 'Hard',
    Karshi_CH: 'Hard',
    Kigali_CH: 'Hard',
    Knoxville_CH: 'Hard',
    Kobe_CH: 'Hard',
    Koblenz_CH: 'Hard',
    Launceston_CH: 'Hard',
    Lexington_CH: 'Hard',
    Liberec_CH: 'Hard',
    Lille_CH: 'Hard',
    Lincoln_CH: 'Hard',
    Little_Rock_CH: 'Hard',
    Manama_CH: 'Hard',
    Manila_CH: 'Hard',
    Maui_CH: 'Hard',
    Meerbusch_CH: 'Hard',
    Mexico_City: 'Hard',
    Mohammedia_CH: 'Hard',
    Mons_CH: 'Hard',
    Mouilleron_CH: 'Hard', Mouilleron_Le_Captif_CH: 'Hard',
    Nonthaburi_CH: 'Hard',
    Noumea_CH: 'Hard',
    Orleans_CH: 'Hard',
    Ottignies: 'Hard', Ottignies_CH: 'Hard',
    Phoenix_CH: 'Hard',
    Prague_CH: 'Hard',
    Qingdao_CH: 'Hard',
    Quimper_CH: 'Hard',
    Rimouski_CH: 'Hard',
    Sacramento_CH: 'Hard',
    Shanghai_CH: 'Hard',
    Shymkent_CH: 'Hard',
    Taipei_CH: 'Hard',
    Tiburon_CH: 'Hard',
    Toyota_CH: 'Hard',
    Vilnius_CH: 'Hard',
    Winnetka_CH: 'Hard',
    Winnipeg_CH: 'Hard',
    Yokkaichi_CH: 'Hard', Yokohama_CH: 'Hard',
    Zhangjiagang_CH: 'Hard',
    Itajai: 'Clay',

    // ── Futures / ITF ──
    Finland_F1: 'Clay', Finland_F3: 'Hard',
    USA_F24: 'Hard', USA_F25: 'Hard',
    ITF_Alkmaar: 'Hard', ITF_Champaign: 'Hard',
    ITF_Martos: 'Clay', ITF_Santo_Domingo: 'Clay',
    ITF_The_Hague: 'Hard', ITF_Tokyo: 'Hard',
    NCAA_Individual_Finals: 'Hard',
};

// ═══════════════════════════════════════════════════════════════
// Surface-Specific Weight Vectors (for Overall Rating)
// ═══════════════════════════════════════════════════════════════
// All vectors sum to 1.00
const SURFACE_WEIGHTS = {
    Hard: {
        serve: 0.12, return_quality: 0.14, ground_consistency: 0.08,
        ground_damage: 0.12, aggression_efficiency: 0.08, volley_win: 0.06,
        volley_usage: 0.03, break_point_defense: 0.09, endurance: 0.08,
        efficiency: 0.08, aggregate_consistency: 0.12,
    },
    Clay: {
        serve: 0.08, return_quality: 0.16, ground_consistency: 0.12,
        ground_damage: 0.10, aggression_efficiency: 0.07, volley_win: 0.04,
        volley_usage: 0.02, break_point_defense: 0.10, endurance: 0.12,
        efficiency: 0.05, aggregate_consistency: 0.14,
    },
    Grass: {
        serve: 0.16, return_quality: 0.10, ground_consistency: 0.06,
        ground_damage: 0.12, aggression_efficiency: 0.08, volley_win: 0.08,
        volley_usage: 0.06, break_point_defense: 0.08, endurance: 0.05,
        efficiency: 0.14, aggregate_consistency: 0.07,
    },
};

// ═══════════════════════════════════════════════════════════════
// OVR Mapping Curve (Piecewise Linear)
// ═══════════════════════════════════════════════════════════════
// Maps percentile rank p ∈ [0,1] of composite score S → OVR ∈ [40,99]
const OVR_CURVE = [
    // { pMin, pMax, ovrMin, ovrMax }
    { pMin: 0.985, pMax: 1.0, ovrMin: 90, ovrMax: 99 },
    { pMin: 0.923, pMax: 0.985, ovrMin: 78, ovrMax: 90 },
    { pMin: 0.77, pMax: 0.923, ovrMin: 65, ovrMax: 78 },
    { pMin: 0.0, pMax: 0.77, ovrMin: 40, ovrMax: 65 },
];

function mapPercentileToOVR(p) {
    for (const seg of OVR_CURVE) {
        if (p >= seg.pMin) {
            const t = (p - seg.pMin) / (seg.pMax - seg.pMin || 1);
            return Math.round(seg.ovrMin + t * (seg.ovrMax - seg.ovrMin));
        }
    }
    return 40;
}

function getSurface(tournamentName) {
    return TOURNAMENT_SURFACE[tournamentName] || 'Hard';
}

module.exports = {
    TOURNAMENT_SURFACE,
    SURFACE_WEIGHTS,
    OVR_CURVE,
    mapPercentileToOVR,
    getSurface,
};
