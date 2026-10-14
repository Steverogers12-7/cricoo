"""Venue normaliser for the API.  Usage:
   from venue_utils import VenueResolver
   r = VenueResolver('venue_mapping.json')
   r.resolve('Wankhede Stadium, Mumbai')            -> ('Wankhede Stadium', 'Mumbai', 'exact')
   r.resolve('wankhede stadum', city='Mumbai')      -> fuzzy match
   r.resolve('Some New Ground', city='Pune')        -> (None, 'Pune', 'city_fallback')
   r.resolve('Some New Ground')                     -> (None, None, 'unknown')  -> use global average
"""
import json, re, difflib
def _key(s): return ' '.join(re.sub(r'[^a-z0-9 ]',' ',s.lower()).split())
class VenueResolver:
    def __init__(self,path,cutoff=0.88):
        d=json.load(open(path,encoding='utf-8')); self.cutoff=cutoff
        self.city=d['canonical_to_city']; self.amb=d['ambiguous_raw_needs_city']
        self.keys={}
        for raw,can in d['raw_to_canonical'].items(): self.keys[_key(raw)]=can
        for can in self.city: self.keys.setdefault(_key(can),can)
        self.cities={_key(c):c for c in set(self.city.values())}
    def resolve(self,venue,city=None):
        k=_key(venue or '')
        c=self.cities.get(_key(city)) if city else None
        if c is None and city: c=city
        if k in self.keys and (not c or self.city[self.keys[k]]==c or _key(f'{k} {c}') not in self.keys):
            can=self.keys[k]; return can,self.city[can],'exact'
        if c and _key(f'{k} {c}') in self.keys: can=self.keys[_key(f'{k} {c}')]; return can,self.city[can],'exact'
        for raw,opts in self.amb.items():            # same raw name used in several cities
            if _key(raw)==k and c:
                for o in opts:
                    if self.city.get(o)==c: return o,c,'exact'
        # try dropping a trailing ", city" typed by the user
        if c and k.endswith(_key(c)):
            k2=k[:-len(_key(c))].strip()
            if k2 in self.keys: can=self.keys[k2]; return can,self.city[can],'exact'
        pool=[x for x in self.keys if not c or self.city[self.keys[x]]==c] or list(self.keys)
        h=difflib.get_close_matches(k,pool,n=1,cutoff=self.cutoff)
        if h: can=self.keys[h[0]]; return can,self.city[can],'fuzzy'
        return (None,c,'city_fallback') if c in set(self.city.values()) else (None,None,'unknown')
