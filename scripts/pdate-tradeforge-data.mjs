import fs from "node:fs/promises";

const INDEX_PATH = "index.html";
const OUTPUT_PATH = "tradeforge-data.json";

const FP_BASE =
  "https://api.fantasypros.com/public/v2/json";

const SLEEPER_BASE =
  "https://api.sleeper.app/v1";

const API_KEY =
  process.env.FANTASYPROS_API_KEY || "";


/* =========================================================
   HELPERS
   ========================================================= */

const clamp =
(v,min,max)=>
Math.max(
  min,
  Math.min(max,v)
);


const round1 =
v=>
Math.round(v*10)/10;


const average =
arr=>
arr.length
?
arr.reduce(
  (a,b)=>a+b,
  0
)/arr.length
:
0;


function standardDeviation(arr){

  if(!arr.length){
    return 0;
  }

  const mean=
    average(arr);

  return Math.sqrt(
    average(
      arr.map(
        x=>
        (x-mean)**2
      )
    )
  );

}


function normName(value){

  return String(value||"")
    .toLowerCase()
    .replace(/[.'’\-]/g,"")
    .replace(
      /\b(jr|sr|ii|iii|iv)\b/g,
      ""
    )
    .replace(
      /[^a-z0-9]/g,
      ""
    );

}


const aliases =
new Map([
  [
    "kennethgainwell",
    "kennygainwell"
  ],
  [
    "kennygainwell",
    "kennygainwell"
  ],
  [
    "joshpalmer",
    "joshuapalmer"
  ],
  [
    "joshuapalmer",
    "joshuapalmer"
  ]
]);


function canonicalName(name){

  const n=
    normName(name);

  return aliases.get(n)||n;

}


/* =========================================================
   FETCH
   ========================================================= */

async function getJSON(
  url,
  options={}
){

  const response=
    await fetch(
      url,
      options
    );


  if(!response.ok){

    throw new Error(
      `${response.status} ${response.statusText}: ${url}`
    );

  }


  return response.json();

}


async function getFantasyPros(
  path,
  params={}
){

  const url=
    new URL(
      FP_BASE+path
    );


  Object.entries(
    params
  )
  .forEach(
    ([key,value])=>{

      if(
        value!==undefined
        &&
        value!==null
        &&
        value!==""
      ){

        url.searchParams.set(
          key,
          String(value)
        );

      }

    }
  );


  return getJSON(
    url,
    {
      headers:{
        "x-api-key":
        API_KEY
      }
    }
  );

}


/* =========================================================
   READ YOUR EXISTING 344 PLAYERS
   ========================================================= */

function parseBaseline(html){

  const players=[];


  const regex=
  /\{\s*rank:\s*(\d+),\s*pos:\s*"([^"]+)",\s*name:\s*"([^"]+)",\s*redraft:\s*([\d.]+),\s*keeper:\s*([\d.]+),\s*dynasty:\s*([\d.]+)\s*\}/g;


  let match;


  while(
    (
      match=
      regex.exec(html)
    )
  ){

    players.push({

      rank:
      Number(match[1]),

      pos:
      match[2],

      name:
      match[3],

      redraft:
      Number(match[4]),

      keeper:
      Number(match[5]),

      dynasty:
      Number(match[6])

    });

  }


  if(
    players.length<300
  ){

    throw new Error(
      `Only found ${players.length} baseline players in index.html`
    );

  }


  return players.sort(
    (a,b)=>
    a.rank-b.rank
  );

}


/* =========================================================
   VALUE CURVES

   We use your existing TradeForge value distribution
   as the normalization curve.

   That means outside rankings are signals.
   They do NOT directly become TradeForge values.
   ========================================================= */

function makeCurves(
  players,
  mode
){

  const global=
    [...players]
    .sort(
      (a,b)=>
      b[mode]-a[mode]
      ||
      a.rank-b.rank
    )
    .map(
      p=>p[mode]
    );


  const byPos={};


  [
    "QB",
    "RB",
    "WR",
    "TE",
    "K",
    "DST"
  ]
  .forEach(
    pos=>{

      byPos[pos]=

      players

      .filter(
        p=>
        p.pos===pos
      )

      .sort(
        (a,b)=>
        b[mode]-a[mode]
        ||
        a.rank-b.rank
      )

      .map(
        p=>p[mode]
      );

    }
  );


  return{
    global,
    byPos
  };

}


function curveValue(
  curve,
  rank
){

  rank=
    Number(rank);


  if(
    !Number.isFinite(rank)
    ||
    rank<1
    ||
    !curve?.length
  ){

    return null;

  }


  const index=
    Math.floor(rank)-1;


  if(
    index<curve.length
  ){

    return curve[index];

  }


  const tail=
    curve[
      curve.length-1
    ]
    ||
    .1;


  return Math.max(
    .1,
    tail
    *
    Math.exp(
      -
      (
        index
        -
        curve.length
        +
        1
      )
      /
      25
    )
  );

}


/* =========================================================
   PROJECTIONS
   ========================================================= */

function projectionPoints(row){

  const stats=
    row?.stats
    ||
    row
    ||
    {};


  const fields=[
    "points_ppr",
    "fantasy_points_ppr",
    "points",
    "fantasy_points"
  ];


  for(
    const field
    of fields
  ){

    if(
      Number.isFinite(
        Number(
          stats[field]
        )
      )
    ){

      return Number(
        stats[field]
      );

    }

  }


  return null;

}


/* =========================================================
   TURN POSITIONAL PRODUCTION INTO A POSITION RANK
   ========================================================= */

function rankByPosition(
  rows,
  nameField,
  positionField,
  valueFunction
){

  const result=
    new Map();


  [
    "QB",
    "RB",
    "WR",
    "TE",
    "K",
    "DST"
  ]
  .forEach(
    pos=>{

      const group=

      rows

      .filter(
        row=>
        String(
          row[positionField]
          ||
          row.position_id
          ||
          ""
        )
        .toUpperCase()
        ===
        pos
      )

      .map(
        row=>({

          row,

          value:
          valueFunction(row)

        })
      )

      .filter(
        item=>
        Number.isFinite(
          item.value
        )
      )

      .sort(
        (a,b)=>
        b.value-a.value
      );


      group.forEach(
        (item,index)=>{

          const name=

          item.row[nameField]
          ||
          item.row.player_name
          ||
          item.row.name;


          if(name){

            result.set(
              canonicalName(name),
              {
                rank:index+1,
                raw:item.value,
                pos
              }
            );

          }

        }
      );

    }
  );


  return result;

}


/* =========================================================
   SLEEPER INJURY AVAILABILITY

   Conservative adjustments.
   ========================================================= */

function sleeperAvailability(player){

  const injury=
    String(
      player?.injury_status
      ||
      ""
    )
    .toLowerCase();


  const status=
    String(
      player?.status
      ||
      ""
    )
    .toLowerCase();


  if(
    status.includes(
      "injured reserve"
    )
    ||
    injury==="ir"
  ){

    return .70;

  }


  if(
    status.includes("pup")
    ||
    injury==="pup"
  ){

    return .72;

  }


  if(
    status.includes(
      "suspend"
    )
  ){

    return .82;

  }


  if(
    injury.includes("out")
  ){

    return .88;

  }


  if(
    injury.includes(
      "doubt"
    )
  ){

    return .92;

  }


  if(
    injury.includes(
      "question"
    )
  ){

    return .97;

  }


  return 1;

}


/* =========================================================
   SLEEPER PLAYER + MARKET MAP
   ========================================================= */

function buildSleeperMaps(
  players,
  adds,
  drops
){

  const byName=
    new Map();


  const byId=
    new Map(
      Object.entries(
        players||{}
      )
    );


  for(
    const [id,player]
    of byId.entries()
  ){

    const name=

      player.full_name

      ||

      [
        player.first_name,
        player.last_name
      ]
      .filter(Boolean)
      .join(" ");


    if(name){

      byName.set(
        canonicalName(name),
        {
          id,
          ...player
        }
      );

    }

  }


  const addMax=
    Math.max(
      1,
      ...
      adds.map(
        x=>
        Number(
          x.count||0
        )
      )
    );


  const dropMax=
    Math.max(
      1,
      ...
      drops.map(
        x=>
        Number(
          x.count||0
        )
      )
    );


  const market=
    new Map();


  const ids=
    new Set([

      ...
      adds.map(
        x=>
        String(
          x.player_id
        )
      ),

      ...
      drops.map(
        x=>
        String(
          x.player_id
        )
      )

    ]);


  for(
    const id
    of ids
  ){

    const addCount=
      Number(
        adds.find(
          x=>
          String(
            x.player_id
          )
          ===
          id
        )
        ?.count
        ||
        0
      );


    const dropCount=
      Number(
        drops.find(
          x=>
          String(
            x.player_id
          )
          ===
          id
        )
        ?.count
        ||
        0
      );


    const addScore=

      Math.log1p(
        addCount
      )

      /

      Math.log1p(
        addMax
      );


    const dropScore=

      Math.log1p(
        dropCount
      )

      /

      Math.log1p(
        dropMax
      );


    market.set(

      id,

      clamp(
        (
          addScore
          -
          dropScore
        )
        *
        .03,
        -.03,
        .03
      )

    );

  }


  return{
    byName,
    market
  };

}


/* =========================================================
   FANTASYPROS LOOKUP MAPS
   ========================================================= */

function makeFantasyProsMetaMap(
  rows=[]
){

  const map=
    new Map();


  rows.forEach(
    player=>{

      const name=
        player.player_name
        ||
        player.name;


      if(name){

        map.set(
          canonicalName(name),
          player
        );

      }

    }
  );


  return map;

}


function makeRankingMap(
  rows=[]
){

  const map=
    new Map();


  rows.forEach(
    player=>{

      const name=
        player.player_name
        ||
        player.name;


      const rank=
        Number(
          player.rank_ecr
          ||
          player.rank
          ||
          player.ecr
        );


      if(
        name
        &&
        Number.isFinite(rank)
      ){

        map.set(
          canonicalName(name),
          rank
        );

      }

    }
  );


  return map;

}


/* =========================================================
   MAIN FEED
   ========================================================= */

async function main(){

  const html=
    await fs.readFile(
      INDEX_PATH,
      "utf8"
    );


  const baseline=
    parseBaseline(html);


  const redraftCurve=
    makeCurves(
      baseline,
      "redraft"
    );


  const dynastyCurve=
    makeCurves(
      baseline,
      "dynasty"
    );


  /* -----------------------------------------
     CURRENT NFL SEASON/WEEK FROM SLEEPER
     ----------------------------------------- */

  const state=
    await getJSON(
      `${SLEEPER_BASE}/state/nfl`
    );


  const season=
    Number(
      state.league_season
      ||
      state.season
      ||
      new Date().getFullYear()
    );


  const week=
    Number(
      state.week
      ||
      1
    );


  /* -----------------------------------------
     SAFE FALLBACK IF NO FANTASYPROS KEY
     ----------------------------------------- */

  if(!API_KEY){

    const payload={

      meta:{

        engineVersion:"1.0",

        generatedAt:
        new Date().toISOString(),

        season,

        week,

        mode:
        "baseline-no-fantasypros-key",

        baselinePlayers:
        baseline.length,

        matchedFantasyPros:0,

        matchedSleeper:0,

        warnings:[
          "FANTASYPROS_API_KEY is not configured. Existing TradeForge values remain unchanged."
        ]

      },

      players:{}

    };


    await fs.writeFile(
      OUTPUT_PATH,
      JSON.stringify(
        payload,
        null,
        2
      )
      +
      "\n"
    );


    console.log(
      "No FantasyPros key configured. Baseline values remain active."
    );


    return;

  }


  /* -----------------------------------------
     SLEEPER DATA
     ----------------------------------------- */

  const [
    sleeperPlayers,
    trendingAdds,
    trendingDrops
  ]

  =

  await Promise.all([

    getJSON(
      `${SLEEPER_BASE}/players/nfl`
    ),

    getJSON(
      `${SLEEPER_BASE}/players/nfl/trending/add?lookback_hours=24&limit=100`
    )
    .catch(
      ()=>[]
    ),

    getJSON(
      `${SLEEPER_BASE}/players/nfl/trending/drop?lookback_hours=24&limit=100`
    )
    .catch(
      ()=>[]
    )

  ]);


  const sleeper=
    buildSleeperMaps(
      sleeperPlayers,
      trendingAdds,
      trendingDrops
    );


  /* -----------------------------------------
     FANTASYPROS DATA
     ----------------------------------------- */

  const startWeek=
    Math.max(
      1,
      week-3
    );


  const endWeek=
    Math.max(
      1,
      week-1
    );


  const requests=

  await Promise.allSettled([

    getFantasyPros(
      "/nfl/players"
    ),

    getFantasyPros(
      `/nfl/${season}/consensus-rankings`,
      {
        position:"ALL",
        scoring:"PPR",
        type:"ROS",
        week
      }
    ),

    getFantasyPros(
      `/nfl/${season}/consensus-rankings`,
      {
        position:"ALL",
        scoring:"PPR",
        type:"DK",
        week
      }
    ),

    getFantasyPros(
      `/nfl/${season}/projections`,
      {
        positions:
        "QB:RB:WR:TE:K:DST",
        scoring:"PPR",
        type:"ROS"
      }
    ),

    getFantasyPros(
      `/nfl/${season}/player-points`,
      {
        start:startWeek,
        end:endWeek,
        scoring:"PPR"
      }
    )

  ]);


  const warnings=[];


  function getRows(
    index,
    name
  ){

    const result=
      requests[index];


    if(
      result.status
      ===
      "rejected"
    ){

      warnings.push(
        `${name}: ${result.reason?.message||result.reason}`
      );

      return[];

    }


    const data=
      result.value
      ||
      {};


    if(
      Array.isArray(data)
    ){

      return data;

    }


    return data.players
      ||
      data.items
      ||
      [];

  }


  const fpPlayers=
    getRows(
      0,
      "players"
    );


  const rosRankings=
    getRows(
      1,
      "ros-rankings"
    );


  const dynastyRankings=
    getRows(
      2,
      "dynasty-rankings"
    );


  const projections=
    getRows(
      3,
      "projections"
    );


  const recentPoints=
    getRows(
      4,
      "recent-points"
    );


  const fpMeta=
    makeFantasyProsMetaMap(
      fpPlayers
    );


  const rosMap=
    makeRankingMap(
      rosRankings
    );


  const dynastyMap=
    makeRankingMap(
      dynastyRankings
    );


  const projectionRanks=
    rankByPosition(
      projections,
      "name",
      "position_id",
      projectionPoints
    );


  const recentRanks=
    rankByPosition(
      recentPoints,
      "player_name",
      "position_id",
      row=>
      Number(
        row.average
        ??
        row.points
      )
    );


  /* =======================================================
     BUILD ENGINE INPUT FOR EVERY TRADEFORGE PLAYER
     ======================================================= */

  const output={};

  let matchedFantasyPros=0;

  let matchedSleeper=0;


  for(
    const player
    of baseline
  ){

    const nameKey=
      canonicalName(
        player.name
      );


    const sleeperPlayer=
      sleeper.byName.get(
        nameKey
      );


    const meta=
      fpMeta.get(
        nameKey
      );


    const rosRank=
      rosMap.get(
        nameKey
      );


    const dynastyRank=
      dynastyMap.get(
        nameKey
      );


    const projectionRank=
      projectionRanks.get(
        nameKey
      );


    const recentRank=
      recentRanks.get(
        nameKey
      );


    if(sleeperPlayer){
      matchedSleeper++;
    }


    if(
      meta
      ||
      rosRank
      ||
      dynastyRank
      ||
      projectionRank
      ||
      recentRank
    ){

      matchedFantasyPros++;

    }


    /* ---------------------------------------
       CONSENSUS
       --------------------------------------- */

    const consensus=

      rosRank

      ?

      curveValue(
        redraftCurve.global,
        rosRank
      )

      :

      player.redraft;


    /* ---------------------------------------
       PROJECTION
       --------------------------------------- */

    const projection=

      projectionRank

      ?

      curveValue(
        redraftCurve.byPos[
          player.pos
        ],
        projectionRank.rank
      )

      :

      consensus;


    /* ---------------------------------------
       RECENT PRODUCTION
       --------------------------------------- */

    const recent=

      recentRank

      ?

      curveValue(
        redraftCurve.byPos[
          player.pos
        ],
        recentRank.rank
      )

      :

      projection;


    /* ---------------------------------------
       OPPORTUNITY PROXY

       Until we connect true routes/snaps/carries,
       this uses projection + recent production.
       --------------------------------------- */

    const opportunity=

      .70
      *
      projection

      +

      .30
      *
      recent;


    /* ---------------------------------------
       ROLE CONFIDENCE

       More agreement among signals =
       higher confidence.
       --------------------------------------- */

    const roleConfidence=

      clamp(

        95

        -

        standardDeviation([
          projection,
          consensus,
          recent
        ])
        *
        2.2,

        50,

        95

      );


    /* ---------------------------------------
       UPSIDE
       --------------------------------------- */

    const upside=

      clamp(

        Math.max(
          projection,
          consensus,
          recent*1.05
        ),

        .1,

        100

      );


    /* ---------------------------------------
       SLEEPER MARKET MOVEMENT
       --------------------------------------- */

    const marketChange=

      sleeperPlayer

      ?

      (
        sleeper.market.get(
          String(
            sleeperPlayer.id
          )
        )
        ||
        0
      )

      :

      0;


    /* ---------------------------------------
       AVAILABILITY
       --------------------------------------- */

    const availability=

      sleeperPlayer

      ?

      sleeperAvailability(
        sleeperPlayer
      )

      :

      1;


    /* ---------------------------------------
       DYNASTY
       --------------------------------------- */

    const dynastyConsensus=

      dynastyRank

      ?

      curveValue(
        dynastyCurve.global,
        dynastyRank
      )

      :

      player.dynasty;


    const year1=

      round1(
        .70
        *
        consensus
        +
        .30
        *
        projection
      );


    const year2=

      round1(
        .65
        *
        dynastyConsensus
        +
        .35
        *
        year1
      );


    const year3=

      round1(
        .80
        *
        dynastyConsensus
        +
        .20
        *
        year1
      );


    const age=
      Number(
        meta?.age
        ??
        sleeperPlayer?.age
      );


    const yearsExp=
      Number(
        sleeperPlayer?.years_exp
      );


    output[nameKey]={

      name:
      player.name,

      pos:
      player.pos,

      team:
      meta?.team_id
      ||
      sleeperPlayer?.team
      ||
      null,

      fantasyProsId:
      meta?.player_id
      ||
      meta?.fpid
      ||
      null,

      sleeperId:
      sleeperPlayer?.id
      ||
      null,


      engine:{

        redraft:{

          projection:
          round1(
            projection
          ),

          consensus:
          round1(
            consensus
          ),

          opportunity:
          round1(
            opportunity
          ),

          recent:
          round1(
            recent
          ),

          availability:
          round1(
            availability
          ),

          roleConfidence:
          round1(
            roleConfidence
          ),

          upside:
          round1(
            upside
          ),

          marketChange:
          Math.round(
            marketChange
            *
            10000
          )
          /
          10000

        },


        dynasty:{

          year1,

          year2,

          year3,

          consensus:
          round1(
            dynastyConsensus
          ),

          roleSecurity:
          round1(
            roleConfidence
          ),

          marketMomentum:
          round1(
            clamp(
              dynastyConsensus
              +
              marketChange*100,
              .1,
              100
            )
          ),

          opportunity:
          round1(
            opportunity
          ),


          ...(

            Number.isFinite(age)

            ?

            {
              age
            }

            :

            {}

          ),


          ...(

            Number.isFinite(
              yearsExp
            )

            ?

            {

              isRookie:
              yearsExp===0,

              nflGames:

              yearsExp===0

              ?

              clamp(
                week-1,
                0,
                8
              )

              :

              8

            }

            :

            {}

          )

        }

      },


      source:{

        rosRank:
        rosRank
        ||
        null,

        dynastyRank:
        dynastyRank
        ||
        null,

        projectionPositionRank:
        projectionRank?.rank
        ||
        null,

        recentPositionRank:
        recentRank?.rank
        ||
        null,

        injuryStatus:
        sleeperPlayer?.injury_status
        ||
        null,

        sleeper24hMarketChange:
        Math.round(
          marketChange
          *
          10000
        )
        /
        10000

      }

    };

  }


  /* =======================================================
     WRITE FINAL FEED
     ======================================================= */

  const payload={

    meta:{

      engineVersion:
      "1.0",

      generatedAt:
      new Date().toISOString(),

      season,

      week,

      mode:
      "fantasypros+sleeper",

      baselinePlayers:
      baseline.length,

      matchedFantasyPros,

      matchedSleeper,

      warnings

    },

    players:
    output

  };


  await fs.writeFile(

    OUTPUT_PATH,

    JSON.stringify(
      payload,
      null,
      2
    )
    +
    "\n"

  );


  console.log(
    `TradeForge feed updated: ${baseline.length} players. FantasyPros matched ${matchedFantasyPros}. Sleeper matched ${matchedSleeper}.`
  );


  if(
    warnings.length
  ){

    console.warn(
      warnings.join("\n")
    );

  }

}


main()
.catch(
  error=>{

    console.error(error);

    process.exit(1);

  }
);
